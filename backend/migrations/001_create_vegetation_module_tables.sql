-- =============================================================================
-- Migration 001: Create Vegetation Prime Module Tables
-- =============================================================================
-- Creates all tables for the Vegetation Prime module with Row Level Security
-- and proper tenant isolation.
--
-- PURPOSE:
-- Initialize database schema for vegetation health monitoring module including:
-- - Configuration storage (Copernicus credentials, preferences)
-- - Job tracking for Sentinel-2 downloads
-- - Scene metadata storage
-- - Index calculation cache
--
-- DEPENDENCIES:
-- - PostgreSQL with PostGIS extension
-- - TimescaleDB (optional, for time-series optimization)
--
-- IDEMPOTENCY:
-- This migration is idempotent - safe to run multiple times
-- =============================================================================

-- Enable PostGIS if not already enabled
CREATE EXTENSION IF NOT EXISTS postgis;

-- =============================================================================
-- Table: vegetation_config
-- Stores tenant-specific configuration (API credentials, preferences)
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Copernicus Data Space credentials (encrypted)
    copernicus_client_id TEXT,
    copernicus_client_secret_encrypted TEXT,  -- Encrypted with tenant-specific key
    
    -- Processing preferences
    default_index_type TEXT DEFAULT 'NDVI' CHECK (default_index_type IN ('NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE')),
    cloud_coverage_threshold DECIMAL(5,2) DEFAULT 20.0 CHECK (cloud_coverage_threshold >= 0 AND cloud_coverage_threshold <= 100),
    auto_process BOOLEAN DEFAULT true,
    
    -- Storage configuration
    storage_type TEXT DEFAULT 's3' CHECK (storage_type IN ('s3', 'minio', 'local')),
    storage_bucket TEXT,
    storage_path TEXT DEFAULT 'vegetation-prime/',
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT,
    
    -- Constraints
    CONSTRAINT vegetation_config_tenant_unique UNIQUE (tenant_id)
);

-- Index for tenant lookups
CREATE INDEX IF NOT EXISTS idx_vegetation_config_tenant ON vegetation_config(tenant_id);

-- =============================================================================
-- Table: vegetation_jobs
-- Tracks asynchronous jobs for Sentinel-2 downloads and processing
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Job metadata
    job_type TEXT NOT NULL CHECK (job_type IN ('download', 'process', 'calculate_index')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    
    -- Job parameters (JSONB for flexibility)
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    -- Entity context (FIWARE)
    entity_id TEXT,  -- AgriParcel ID
    entity_type TEXT DEFAULT 'AgriParcel',
    
    -- Geographic bounds (PostGIS geometry)
    bounds GEOMETRY(POLYGON, 4326),
    
    -- Date range for scene search
    start_date DATE,
    end_date DATE,
    
    -- Processing results
    result JSONB,
    error_message TEXT,
    error_traceback TEXT,
    
    -- Celery task tracking
    celery_task_id TEXT,
    
    -- Progress tracking
    progress_percentage INTEGER DEFAULT 0 CHECK (progress_percentage >= 0 AND progress_percentage <= 100),
    progress_message TEXT,
    
    -- Timing
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Metadata
    created_by TEXT
);

-- Indexes for vegetation_jobs
CREATE INDEX IF NOT EXISTS idx_vegetation_jobs_tenant ON vegetation_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_jobs_status ON vegetation_jobs(status);
CREATE INDEX IF NOT EXISTS idx_vegetation_jobs_entity ON vegetation_jobs(entity_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_jobs_created ON vegetation_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vegetation_jobs_celery_task ON vegetation_jobs(celery_task_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_jobs_bounds ON vegetation_jobs USING GIST(bounds);

-- =============================================================================
-- Table: vegetation_scenes
-- Stores metadata for downloaded Sentinel-2 scenes
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_scenes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Sentinel-2 metadata
    scene_id TEXT NOT NULL,  -- Sentinel-2 product ID
    product_type TEXT DEFAULT 'S2MSI2A',  -- L2A product
    platform TEXT DEFAULT 'Sentinel-2',
    
    -- Temporal information
    sensing_date DATE NOT NULL,
    ingestion_date TIMESTAMPTZ,
    
    -- Geographic information
    footprint GEOMETRY(POLYGON, 4326) NOT NULL,
    centroid GEOMETRY(POINT, 4326),
    
    -- Cloud information
    cloud_coverage DECIMAL(5,2) CHECK (cloud_coverage >= 0 AND cloud_coverage <= 100),
    snow_coverage DECIMAL(5,2) CHECK (snow_coverage >= 0 AND snow_coverage <= 100),
    
    -- Storage information
    storage_path TEXT NOT NULL,  -- S3/MinIO path
    storage_bucket TEXT,
    file_size_bytes BIGINT,
    
    -- Band information (JSONB array)
    bands JSONB,  -- {"B02": "path/to/B02.tif", "B03": "...", ...}
    
    -- Quality flags
    is_valid BOOLEAN DEFAULT true,
    quality_flags JSONB DEFAULT '{}'::jsonb,
    
    -- Job reference
    job_id UUID REFERENCES vegetation_jobs(id) ON DELETE SET NULL,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT vegetation_scenes_tenant_scene_unique UNIQUE (tenant_id, scene_id)
);

-- Indexes for vegetation_scenes
CREATE INDEX IF NOT EXISTS idx_vegetation_scenes_tenant ON vegetation_scenes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_scenes_sensing_date ON vegetation_scenes(sensing_date DESC);
CREATE INDEX IF NOT EXISTS idx_vegetation_scenes_footprint ON vegetation_scenes USING GIST(footprint);
CREATE INDEX IF NOT EXISTS idx_vegetation_scenes_centroid ON vegetation_scenes USING GIST(centroid);
CREATE INDEX IF NOT EXISTS idx_vegetation_scenes_job ON vegetation_scenes(job_id);

-- =============================================================================
-- Table: vegetation_indices_cache
-- Caches pre-calculated index values for fast retrieval
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_indices_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Scene reference
    scene_id UUID NOT NULL REFERENCES vegetation_scenes(id) ON DELETE CASCADE,
    
    -- Entity context
    entity_id TEXT,  -- AgriParcel ID
    entity_type TEXT DEFAULT 'AgriParcel',
    
    -- Index information
    index_type TEXT NOT NULL CHECK (index_type IN ('NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE', 'CUSTOM')),
    formula TEXT,  -- For custom indices
    
    -- Calculated values
    mean_value DECIMAL(10,6),
    min_value DECIMAL(10,6),
    max_value DECIMAL(10,6),
    std_dev DECIMAL(10,6),
    pixel_count INTEGER,
    
    -- Spatial aggregation (GeoJSON)
    statistics_geojson JSONB,  -- Zonal statistics per polygon
    
    -- Storage information
    result_raster_path TEXT,  -- Path to calculated raster (COG)
    result_tiles_path TEXT,  -- Path to XYZ tiles directory
    
    -- Processing metadata
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    calculation_time_ms INTEGER,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT vegetation_indices_cache_scene_index_unique UNIQUE (scene_id, index_type, entity_id, formula)
);

-- Indexes for vegetation_indices_cache
CREATE INDEX IF NOT EXISTS idx_vegetation_indices_cache_tenant ON vegetation_indices_cache(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_indices_cache_scene ON vegetation_indices_cache(scene_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_indices_cache_entity ON vegetation_indices_cache(entity_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_indices_cache_type ON vegetation_indices_cache(index_type);
CREATE INDEX IF NOT EXISTS idx_vegetation_indices_cache_calculated ON vegetation_indices_cache(calculated_at DESC);

-- =============================================================================
-- Table: vegetation_custom_formulas
-- Stores user-defined custom index formulas
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_custom_formulas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Formula metadata
    name TEXT NOT NULL,
    description TEXT,
    formula TEXT NOT NULL,  -- Safe mathematical expression
    
    -- Validation
    is_validated BOOLEAN DEFAULT false,
    validation_error TEXT,
    
    -- Usage tracking
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT,
    
    -- Constraints
    CONSTRAINT vegetation_custom_formulas_tenant_name_unique UNIQUE (tenant_id, name)
);

-- Indexes for vegetation_custom_formulas
CREATE INDEX IF NOT EXISTS idx_vegetation_custom_formulas_tenant ON vegetation_custom_formulas(tenant_id);

-- =============================================================================
-- Row Level Security (RLS) Policies
-- =============================================================================

-- Enable RLS on all tables
ALTER TABLE vegetation_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_scenes ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_indices_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_custom_formulas ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS vegetation_config_policy ON vegetation_config;
DROP POLICY IF EXISTS vegetation_jobs_policy ON vegetation_jobs;
DROP POLICY IF EXISTS vegetation_scenes_policy ON vegetation_scenes;
DROP POLICY IF EXISTS vegetation_indices_cache_policy ON vegetation_indices_cache;
DROP POLICY IF EXISTS vegetation_custom_formulas_policy ON vegetation_custom_formulas;

-- Policy: vegetation_config
CREATE POLICY vegetation_config_policy ON vegetation_config
    USING (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    );

-- Policy: vegetation_jobs
CREATE POLICY vegetation_jobs_policy ON vegetation_jobs
    USING (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    );

-- Policy: vegetation_scenes
CREATE POLICY vegetation_scenes_policy ON vegetation_scenes
    USING (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    );

-- Policy: vegetation_indices_cache
CREATE POLICY vegetation_indices_cache_policy ON vegetation_indices_cache
    USING (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    );

-- Policy: vegetation_custom_formulas
CREATE POLICY vegetation_custom_formulas_policy ON vegetation_custom_formulas
    USING (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    )
    WITH CHECK (
        tenant_id = current_setting('app.current_tenant', true) OR
        current_setting('app.current_tenant', true) = 'platform' OR
        current_setting('app.current_tenant', true) = 'platform_admin'
    );

-- =============================================================================
-- Functions and Triggers
-- =============================================================================

-- Function: Update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for updated_at
DROP TRIGGER IF EXISTS update_vegetation_config_updated_at ON vegetation_config;
CREATE TRIGGER update_vegetation_config_updated_at
    BEFORE UPDATE ON vegetation_config
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_vegetation_jobs_updated_at ON vegetation_jobs;
CREATE TRIGGER update_vegetation_jobs_updated_at
    BEFORE UPDATE ON vegetation_jobs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_vegetation_scenes_updated_at ON vegetation_scenes;
CREATE TRIGGER update_vegetation_scenes_updated_at
    BEFORE UPDATE ON vegetation_scenes
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_vegetation_indices_cache_updated_at ON vegetation_indices_cache;
CREATE TRIGGER update_vegetation_indices_cache_updated_at
    BEFORE UPDATE ON vegetation_indices_cache
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_vegetation_custom_formulas_updated_at ON vegetation_custom_formulas;
CREATE TRIGGER update_vegetation_custom_formulas_updated_at
    BEFORE UPDATE ON vegetation_custom_formulas
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- End of migration 001
-- =============================================================================

