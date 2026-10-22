-- =============================================================================
-- Rollback Migration 001: Drop Vegetation Prime Module Tables
-- =============================================================================
-- Safely removes all tables and related objects created by migration 001
--
-- WARNING: This will delete all data in these tables!
-- =============================================================================

-- Drop triggers first
DROP TRIGGER IF EXISTS update_vegetation_custom_formulas_updated_at ON vegetation_custom_formulas;
DROP TRIGGER IF EXISTS update_vegetation_indices_cache_updated_at ON vegetation_indices_cache;
DROP TRIGGER IF EXISTS update_vegetation_scenes_updated_at ON vegetation_scenes;
DROP TRIGGER IF EXISTS update_vegetation_jobs_updated_at ON vegetation_jobs;
DROP TRIGGER IF EXISTS update_vegetation_config_updated_at ON vegetation_config;

-- Drop function
DROP FUNCTION IF EXISTS update_updated_at_column();

-- Drop policies
DROP POLICY IF EXISTS vegetation_custom_formulas_policy ON vegetation_custom_formulas;
DROP POLICY IF EXISTS vegetation_indices_cache_policy ON vegetation_indices_cache;
DROP POLICY IF EXISTS vegetation_scenes_policy ON vegetation_scenes;
DROP POLICY IF EXISTS vegetation_jobs_policy ON vegetation_jobs;
DROP POLICY IF EXISTS vegetation_config_policy ON vegetation_config;

-- Drop tables (CASCADE to handle foreign keys)
DROP TABLE IF EXISTS vegetation_custom_formulas CASCADE;
DROP TABLE IF EXISTS vegetation_indices_cache CASCADE;
DROP TABLE IF EXISTS vegetation_scenes CASCADE;
DROP TABLE IF EXISTS vegetation_jobs CASCADE;
DROP TABLE IF EXISTS vegetation_config CASCADE;

-- =============================================================================
-- End of rollback migration 001
-- =============================================================================

