-- =============================================================================
-- Migration 006: Add acquisition_datetime to vegetation_scenes
-- =============================================================================
-- Stores exact STAC acquisition time (e.g. 2026-02-15T10:51:31Z) for use in
-- TimescaleDB telemetry and correlation with other sensors. Avoids truncating
-- to midnight UTC which loses solar angle and illumination metadata.
--
-- IDEMPOTENT: Safe to run multiple times.
-- =============================================================================

ALTER TABLE vegetation_scenes
ADD COLUMN IF NOT EXISTS acquisition_datetime TIMESTAMPTZ;

COMMENT ON COLUMN vegetation_scenes.acquisition_datetime IS
  'Exact acquisition datetime from STAC (e.g. Sentinel-2 overpass time). Used for telemetry time and DataHub.';
