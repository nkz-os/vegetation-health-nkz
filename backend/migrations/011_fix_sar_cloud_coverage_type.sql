-- Fix SAR cloud_coverage type: SAR scenes store "N/A" as text
-- Must drop CHECK constraint (uses numeric >=) before altering type
ALTER TABLE vegetation_scenes DROP CONSTRAINT IF EXISTS vegetation_scenes_cloud_coverage_check;
ALTER TABLE vegetation_scenes ALTER COLUMN cloud_coverage TYPE TEXT USING COALESCE(cloud_coverage::text, ''::text);

-- Add new constraint: allow numeric-like strings or 'N/A'
ALTER TABLE vegetation_scenes ADD CONSTRAINT vegetation_scenes_cloud_coverage_check
    CHECK (cloud_coverage ~ '^\d+(\.\d+)?$' OR cloud_coverage = 'N/A');
