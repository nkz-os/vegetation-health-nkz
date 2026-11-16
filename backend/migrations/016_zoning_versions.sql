-- 016_zoning_versions.sql
-- Zoning versions (current, pending proposal, history) and technician notes.
-- Only the current zoning lives in the context broker; the rest lives here.
-- Expand-only and idempotent.

CREATE TABLE IF NOT EXISTS zoning_versions (
    run_id          TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    parcel_id       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('current', 'pending', 'superseded', 'discarded')),
    source          TEXT NOT NULL CHECK (source IN ('proposal', 'merge')),
    derived_from    TEXT NULL,
    -- pending only: the current run this proposal was set aside against
    parked_on_run_id TEXT NULL,
    index_type      TEXT NULL,
    outcome_reason  TEXT NULL,
    entities        JSONB NOT NULL DEFAULT '[]'::jsonb,
    job_id          TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at     TIMESTAMPTZ NULL,
    accepted_by     TEXT NULL
);

CREATE INDEX IF NOT EXISTS ix_zoning_versions_parcel
    ON zoning_versions (tenant_id, parcel_id, created_at DESC);

-- At most one current and one pending version per parcel.
CREATE UNIQUE INDEX IF NOT EXISTS ux_zoning_versions_current
    ON zoning_versions (tenant_id, parcel_id)
    WHERE status = 'current';

CREATE UNIQUE INDEX IF NOT EXISTS ux_zoning_versions_pending
    ON zoning_versions (tenant_id, parcel_id)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS zoning_notes (
    id          UUID PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    parcel_id   TEXT NOT NULL,
    zone_id     TEXT NOT NULL,
    geometry    JSONB NOT NULL,
    cause       TEXT NOT NULL CHECK (char_length(cause) BETWEEN 1 AND 500),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  TEXT NULL
);

CREATE INDEX IF NOT EXISTS ix_zoning_notes_parcel
    ON zoning_notes (tenant_id, parcel_id, created_at DESC);

-- Tenant isolation (same convention as migration 012).
ALTER TABLE zoning_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE zoning_notes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'zoning_versions' AND policyname = 'tenant_isolation_zoning_versions'
    ) THEN
        CREATE POLICY tenant_isolation_zoning_versions ON zoning_versions
            USING (tenant_id = current_setting('app.current_tenant')::text);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'zoning_notes' AND policyname = 'tenant_isolation_zoning_notes'
    ) THEN
        CREATE POLICY tenant_isolation_zoning_notes ON zoning_notes
            USING (tenant_id = current_setting('app.current_tenant')::text);
    END IF;
END $$;
