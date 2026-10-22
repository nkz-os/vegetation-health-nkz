-- 014_add_osavi_index.sql
-- Allow OSAVI (Optimized Soil-Adjusted Vegetation Index) in the index cache.
-- Expand-only: existing values are kept.

ALTER TABLE vegetation_indices_cache DROP CONSTRAINT IF EXISTS vegetation_indices_cache_index_type_check;

ALTER TABLE vegetation_indices_cache ADD CONSTRAINT vegetation_indices_cache_index_type_check
    CHECK (index_type IN ('NDVI', 'EVI', 'SAVI', 'OSAVI', 'GNDVI', 'NDRE', 'CUSTOM'));
