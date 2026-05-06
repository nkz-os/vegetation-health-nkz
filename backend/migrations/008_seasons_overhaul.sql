-- 008_seasons_overhaul.sql
-- Make crop_season the canonical grouping for vegetation_jobs:
--   * jobs gain crop_season_id FK (nullable for legacy rows)
--   * seasons cannot overlap on the same parcel (active rows only)
--   * soft-delete columns prepared for cascade flow
-- Idempotent: safe to re-run.

-- Required for EXCLUDE constraint on date ranges
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 1. Job → season FK
ALTER TABLE vegetation_jobs
    ADD COLUMN IF NOT EXISTS crop_season_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vegetation_jobs_crop_season_fkey'
    ) THEN
        ALTER TABLE vegetation_jobs
            ADD CONSTRAINT vegetation_jobs_crop_season_fkey
            FOREIGN KEY (crop_season_id)
            REFERENCES vegetation_crop_seasons(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_vegetation_jobs_season
    ON vegetation_jobs(crop_season_id);

-- 2. Soft-delete bookkeeping columns
ALTER TABLE vegetation_jobs
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;
ALTER TABLE vegetation_crop_seasons
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;

-- 3. Replace the old uniqueness with strict no-overlap among ACTIVE seasons
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_entity_crop_period'
    ) THEN
        ALTER TABLE vegetation_crop_seasons DROP CONSTRAINT uq_entity_crop_period;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vegetation_crop_seasons_no_overlap'
    ) THEN
        ALTER TABLE vegetation_crop_seasons
            ADD CONSTRAINT vegetation_crop_seasons_no_overlap
            EXCLUDE USING gist (
                tenant_id WITH =,
                entity_id WITH =,
                daterange(start_date, COALESCE(end_date, 'infinity'::date), '[]') WITH &&
            ) WHERE (deleted_at IS NULL);
    END IF;
END $$;
