-- 017_parcel_scenes.sql
-- Per-parcel scene status: one row per (tenant, parcel, scene) with the verdict
-- of that parcel for that scene. Process state, not an observed series.
-- Expand-only and idempotent; the backfill never fails on malformed values.

CREATE TABLE IF NOT EXISTS vegetation_parcel_scenes (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT NOT NULL,
    entity_id        TEXT NOT NULL,
    scene_id         TEXT NOT NULL,
    sensing_date     DATE NULL,
    status           TEXT NOT NULL CHECK (status IN ('pending', 'done', 'cloud_skipped', 'no_valid_pixels', 'failed')),
    parcel_cloud_pct NUMERIC(5, 2) NULL,
    job_id           UUID NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ux_vegetation_parcel_scenes UNIQUE (tenant_id, entity_id, scene_id)
);

CREATE INDEX IF NOT EXISTS ix_vegetation_parcel_scenes_date
    ON vegetation_parcel_scenes (tenant_id, entity_id, sensing_date);

ALTER TABLE vegetation_parcel_scenes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'vegetation_parcel_scenes' AND policyname = 'tenant_isolation_parcel_scenes'
    ) THEN
        CREATE POLICY tenant_isolation_parcel_scenes ON vegetation_parcel_scenes
            USING (tenant_id = current_setting('app.current_tenant')::text);
    END IF;
END $$;

-- Backfill from finished jobs so already-done work is not redone.
-- Casts go through session-local helpers that return NULL instead of raising:
-- a failing migration stops the API at startup.
CREATE FUNCTION pg_temp.safe_date(v TEXT) RETURNS DATE AS $$
BEGIN
    IF v IS NULL OR v !~ '^\d{4}-\d{2}-\d{2}' THEN
        RETURN NULL;
    END IF;
    RETURN substring(v from 1 for 10)::date;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END
$$ LANGUAGE plpgsql;

CREATE FUNCTION pg_temp.safe_pct(v TEXT) RETURNS NUMERIC AS $$
BEGIN
    IF v IS NULL OR v !~ '^-?\d+(\.\d+)?$' THEN
        RETURN NULL;
    END IF;
    IF v::numeric < 0 OR v::numeric > 100 THEN
        RETURN NULL;
    END IF;
    RETURN round(v::numeric, 2);
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END
$$ LANGUAGE plpgsql;

INSERT INTO vegetation_parcel_scenes
    (tenant_id, entity_id, scene_id, sensing_date, status, parcel_cloud_pct, job_id, attempts, updated_at)
SELECT DISTINCT ON (c.tenant_id, c.entity_id, c.scene_id)
    c.tenant_id, c.entity_id, c.scene_id, c.sensing_date, c.status, c.parcel_cloud_pct, c.job_id, 0,
    CASE WHEN c.abandoned THEN now() - interval '7 days' ELSE now() END
FROM (
    -- Finished calculations: done (also those skipped by the idempotency guard).
    -- Manual API jobs carry the scene UUID in parameters.scene_id: map it to the product id.
    SELECT j.tenant_id, j.entity_id,
           COALESCE(vs.scene_id, j.parameters->>'scene_id') AS scene_id,
           pg_temp.safe_date(COALESCE(j.parameters->>'date', j.parameters->>'sensing_date',
                                      j.result->>'sensing_date')) AS sensing_date,
           'done'::text AS status,
           NULL::numeric AS parcel_cloud_pct,
           j.id AS job_id,
           j.created_at,
           false AS abandoned
    FROM vegetation_jobs j
    LEFT JOIN vegetation_scenes vs
           ON vs.id::text = j.parameters->>'scene_id' AND vs.tenant_id = j.tenant_id
    WHERE j.job_type = 'calculate_index'
      AND j.status = 'completed'
      AND j.entity_id IS NOT NULL
      AND j.parameters->>'scene_id' IS NOT NULL
      AND (j.result->>'raster_path' IS NOT NULL OR j.result->>'skipped' = 'true')
    UNION ALL
    SELECT j.tenant_id, j.entity_id,
           COALESCE(j.parameters->>'scene_id', j.result->>'scene_product_id') AS scene_id,
           pg_temp.safe_date(COALESCE(j.parameters->>'date', j.parameters->>'sensing_date',
                                      j.result->>'sensing_date')) AS sensing_date,
           CASE
               WHEN j.status = 'completed' AND j.result->>'skipped_due_to_clouds' = 'true' THEN 'cloud_skipped'
               WHEN j.status = 'failed' THEN 'failed'
               ELSE 'pending'
           END AS status,
           CASE WHEN j.status = 'completed' AND j.result->>'skipped_due_to_clouds' = 'true'
                THEN pg_temp.safe_pct(j.result->>'local_cloud_pct') END AS parcel_cloud_pct,
           j.id AS job_id,
           j.created_at,
           -- abandoned: failed, or a completed download that never produced a calculation.
           -- In-flight (pending/running) downloads keep a fresh timestamp.
           (j.status = 'failed'
            OR (j.status = 'completed' AND COALESCE(j.result->>'skipped_due_to_clouds', '') <> 'true')) AS abandoned
    FROM vegetation_jobs j
    WHERE j.job_type = 'download'
      AND j.entity_id IS NOT NULL
      AND COALESCE(j.parameters->>'scene_id', j.result->>'scene_product_id') IS NOT NULL
) c
ORDER BY c.tenant_id, c.entity_id, c.scene_id,
         CASE c.status WHEN 'done' THEN 0 WHEN 'cloud_skipped' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
         c.created_at DESC
ON CONFLICT DO NOTHING;

DROP FUNCTION pg_temp.safe_date(TEXT);
DROP FUNCTION pg_temp.safe_pct(TEXT);
