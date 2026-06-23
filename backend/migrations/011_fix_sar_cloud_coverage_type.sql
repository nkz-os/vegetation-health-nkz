-- Fix SAR cloud_coverage type: SAR scenes store "N/A" as text
ALTER TABLE vegetation_scenes ALTER COLUMN cloud_coverage TYPE TEXT USING COALESCE(cloud_coverage::text, ''::text);
