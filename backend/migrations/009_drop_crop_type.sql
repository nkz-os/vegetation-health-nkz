-- 009_drop_crop_type.sql
-- Remove crop_type from vegetation monitoring periods.
-- The crop is now read from AgriParcel.hasAgriCrop in Orion-LD.
-- Rename table to reflect that these are date-range monitoring periods,
-- not agricultural crop seasons with a hardcoded crop type.

-- Drop constraints that referenced the old table name before renaming
ALTER TABLE vegetation_crop_seasons DROP CONSTRAINT IF EXISTS vegetation_crop_seasons_no_overlap;
ALTER TABLE vegetation_crop_seasons DROP CONSTRAINT IF EXISTS uq_entity_crop_period;

-- Rename table to reflect the new semantics
ALTER TABLE vegetation_crop_seasons RENAME TO vegetation_monitoring_periods;

-- Remove hardcoded crop_type column
ALTER TABLE vegetation_monitoring_periods DROP COLUMN IF EXISTS crop_type;
