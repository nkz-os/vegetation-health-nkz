-- =============================================================================
-- Migration 003: Create Global Scene Cache Table
-- =============================================================================
-- Creates a global cache table for Sentinel-2 scenes shared across all tenants.
-- This allows multiple tenants to reuse the same downloaded scenes without
-- re-downloading from Copernicus, saving quota.
--
-- PURPOSE:
-- - Store metadata about scenes downloaded from Copernicus in a global bucket
-- - Enable scene reuse across tenants (copy from global to tenant bucket)
-- - Track download/reuse statistics
--
-- DEPENDENCIES:
-- - Migration 001 (base tables)
--
-- IDEMPOTENCY:
-- This migration is idempotent - safe to run multiple times
-- =============================================================================

-- =============================================================================
-- Table: global_scene_cache
-- Stores metadata for Sentinel-2 scenes in the global shared bucket
-- =============================================================================
CREATE TABLE IF NOT EXISTS global_scene_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Sentinel-2 scene identifier (unique product ID from Copernicus)
    scene_id TEXT NOT NULL UNIQUE,
    product_type TEXT DEFAULT 'S2MSI2A' NOT NULL,
    platform TEXT DEFAULT 'Sentinel-2' NOT NULL,
    
    -- Temporal information
    sensing_date DATE NOT NULL,
    ingestion_date TIMESTAMPTZ,
    
    -- Storage information (global bucket)
    storage_path TEXT NOT NULL,  -- Path in global bucket
    storage_bucket TEXT NOT NULL,  -- Global bucket name (e.g., 'vegetation-prime-global')
    file_size_bytes BIGINT,
    
    -- Band information (paths to raw bands in global bucket)
    bands JSONB,  -- {"B02": "path/to/B02.tif", "B04": "...", ...}
    
    -- Metadata from Copernicus
    cloud_coverage TEXT,
    snow_coverage TEXT,
    footprint_geometry TEXT,  -- GeoJSON string (can be converted to PostGIS if needed)
    
    -- Cache metadata
    download_count INTEGER DEFAULT 0 NOT NULL,  -- How many times this scene has been reused
    last_accessed_at TIMESTAMPTZ,  -- Last time a tenant requested this scene
    is_valid BOOLEAN DEFAULT true NOT NULL,  -- Mark as invalid if files are corrupted/missing
    
    -- Quality flags
    quality_flags JSONB DEFAULT '{}' NOT NULL,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT global_scene_cache_scene_id_unique UNIQUE (scene_id)
);

-- Indexes for global_scene_cache
CREATE INDEX IF NOT EXISTS idx_global_scene_cache_scene_id ON global_scene_cache(scene_id);
CREATE INDEX IF NOT EXISTS idx_global_scene_cache_sensing_date ON global_scene_cache(sensing_date DESC);
CREATE INDEX IF NOT EXISTS idx_global_scene_cache_is_valid ON global_scene_cache(is_valid);
CREATE INDEX IF NOT EXISTS idx_global_scene_cache_last_accessed ON global_scene_cache(last_accessed_at DESC);

-- Comment on table
COMMENT ON TABLE global_scene_cache IS 'Global cache for Sentinel-2 scenes shared across all tenants. Scenes are stored in a global bucket and can be copied to tenant buckets when requested.';

-- Comment on columns
COMMENT ON COLUMN global_scene_cache.scene_id IS 'Unique Sentinel-2 product ID from Copernicus (e.g., S2A_MSIL2A_20231201T120000_N0509_R123_T31TFJ_20231201T140000)';
COMMENT ON COLUMN global_scene_cache.storage_bucket IS 'Global bucket name where raw scene files are stored (e.g., vegetation-prime-global)';
COMMENT ON COLUMN global_scene_cache.storage_path IS 'Base path in global bucket where scene files are stored';
COMMENT ON COLUMN global_scene_cache.bands IS 'JSON object mapping band names to their storage paths in global bucket';
COMMENT ON COLUMN global_scene_cache.download_count IS 'Number of times this scene has been reused (copied to tenant buckets)';
COMMENT ON COLUMN global_scene_cache.last_accessed_at IS 'Last time a tenant requested this scene';
COMMENT ON COLUMN global_scene_cache.is_valid IS 'Set to false if scene files are corrupted or missing';















