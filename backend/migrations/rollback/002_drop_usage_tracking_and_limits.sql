-- =============================================================================
-- Rollback Migration 002: Drop Usage Tracking and Limits Tables
-- =============================================================================
-- Safely removes all tables and related objects created by migration 002
--
-- WARNING: This will delete all usage tracking data!
-- =============================================================================

-- Drop triggers
DROP TRIGGER IF EXISTS update_vegetation_usage_stats_updated_at ON vegetation_usage_stats;
DROP TRIGGER IF EXISTS update_vegetation_plan_limits_updated_at ON vegetation_plan_limits;

-- Drop function
DROP FUNCTION IF EXISTS get_or_create_usage_stats;

-- Drop policies
DROP POLICY IF EXISTS vegetation_usage_log_policy ON vegetation_usage_log;
DROP POLICY IF EXISTS vegetation_usage_stats_policy ON vegetation_usage_stats;
DROP POLICY IF EXISTS vegetation_plan_limits_policy ON vegetation_plan_limits;

-- Drop tables (CASCADE to handle foreign keys)
DROP TABLE IF EXISTS vegetation_usage_log CASCADE;
DROP TABLE IF EXISTS vegetation_usage_stats CASCADE;
DROP TABLE IF EXISTS vegetation_plan_limits CASCADE;

-- =============================================================================
-- End of rollback migration 002
-- =============================================================================



