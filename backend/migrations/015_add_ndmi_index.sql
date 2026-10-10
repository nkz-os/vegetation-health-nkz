-- 015_add_ndmi_index.sql
-- Allow NDMI (Normalized Difference Moisture Index) in the index cache.
-- Expand-only: existing values are kept.

ALTER TABLE vegetation_indices_cache DROP CONSTRAINT IF EXISTS vegetation_indices_cache_index_type_check;

ALTER TABLE vegetation_indices_cache ADD CONSTRAINT vegetation_indices_cache_index_type_check
    CHECK (index_type IN ('NDVI', 'EVI', 'SAVI', 'OSAVI', 'GNDVI', 'NDRE', 'NDMI', 'CUSTOM'));
