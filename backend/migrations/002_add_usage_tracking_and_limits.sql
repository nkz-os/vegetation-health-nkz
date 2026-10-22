-- =============================================================================
-- Migration 002: Add Usage Tracking and Limits Tables
-- =============================================================================
-- Creates tables for monetization: usage tracking and plan limits
--
-- PURPOSE:
-- Enable double-layer limits (volume in Ha + frequency in jobs/day)
-- Track usage for billing and rate limiting
--
-- DEPENDENCIES:
-- - Migration 001 (vegetation tables)
--
-- IDEMPOTENCY:
-- This migration is idempotent - safe to run multiple times
-- =============================================================================

-- =============================================================================
-- Table: vegetation_plan_limits
-- Stores plan limits synchronized from Core Platform
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_plan_limits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Plan information
    plan_type TEXT NOT NULL DEFAULT 'basic',  -- basic, premium, enterprise
    plan_name TEXT,
    
    -- Volume limits (Hectares)
    monthly_ha_limit DECIMAL(12, 2) DEFAULT 10.0,  -- Default: 10 Ha/month
    daily_ha_limit DECIMAL(12, 2) DEFAULT 5.0,   -- Default: 5 Ha/day
    
    -- Frequency limits (Jobs per day)
    daily_jobs_limit INTEGER DEFAULT 5,           -- Default: 5 jobs/day
    monthly_jobs_limit INTEGER DEFAULT 100,       -- Default: 100 jobs/month
    
    -- Rate limiting (per job type)
    daily_download_jobs_limit INTEGER DEFAULT 3,
    daily_process_jobs_limit INTEGER DEFAULT 10,
    daily_calculate_jobs_limit INTEGER DEFAULT 20,
    
    -- Metadata
    is_active BOOLEAN DEFAULT true,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_by TEXT,  -- Core platform identifier
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT vegetation_plan_limits_tenant_unique UNIQUE (tenant_id)
);

-- Index for tenant lookups
CREATE INDEX IF NOT EXISTS idx_vegetation_plan_limits_tenant ON vegetation_plan_limits(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_plan_limits_active ON vegetation_plan_limits(is_active);

-- =============================================================================
-- Table: vegetation_usage_stats
-- Monthly aggregated usage statistics per tenant
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_usage_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Time period
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    
    -- Volume usage (Hectares)
    ha_processed DECIMAL(12, 4) DEFAULT 0.0,
    ha_processed_count INTEGER DEFAULT 0,  -- Number of jobs that contributed
    
    -- Frequency usage (Jobs)
    jobs_created INTEGER DEFAULT 0,
    jobs_completed INTEGER DEFAULT 0,
    jobs_failed INTEGER DEFAULT 0,
    
    -- Breakdown by job type
    download_jobs INTEGER DEFAULT 0,
    process_jobs INTEGER DEFAULT 0,
    calculate_jobs INTEGER DEFAULT 0,
    
    -- Metadata
    first_job_at TIMESTAMPTZ DEFAULT NOW(),
    last_job_at TIMESTAMPTZ,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT vegetation_usage_stats_tenant_period_unique UNIQUE (tenant_id, year, month)
);

-- Indexes for vegetation_usage_stats
CREATE INDEX IF NOT EXISTS idx_vegetation_usage_stats_tenant ON vegetation_usage_stats(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_usage_stats_period ON vegetation_usage_stats(year, month DESC);
CREATE INDEX IF NOT EXISTS idx_vegetation_usage_stats_tenant_period ON vegetation_usage_stats(tenant_id, year, month DESC);

-- =============================================================================
-- Table: vegetation_usage_log
-- Detailed log of each job's usage (for auditing and debugging)
-- =============================================================================
CREATE TABLE IF NOT EXISTS vegetation_usage_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    
    -- Job reference
    job_id UUID NOT NULL REFERENCES vegetation_jobs(id) ON DELETE CASCADE,
    
    -- Usage metrics
    ha_processed DECIMAL(12, 4) DEFAULT 0.0,
    job_type TEXT NOT NULL,
    
    -- Timestamp
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Metadata
    bounds GEOMETRY(POLYGON, 4326)  -- For area calculation verification
);

-- Indexes for vegetation_usage_log
CREATE INDEX IF NOT EXISTS idx_vegetation_usage_log_tenant ON vegetation_usage_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_usage_log_job ON vegetation_usage_log(job_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_usage_log_processed ON vegetation_usage_log(processed_at DESC);

-- =============================================================================
-- Row Level Security (RLS) Policies
-- =============================================================================

-- Enable RLS on all tables
ALTER TABLE vegetation_plan_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_usage_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_usage_log ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS vegetation_plan_limits_policy ON vegetation_plan_limits;
DROP POLICY IF EXISTS vegetation_usage_stats_policy ON vegetation_usage_stats;
DROP POLICY IF EXISTS vegetation_usage_log_policy ON vegetation_usage_log;

-- Policy: vegetation_plan_limits
CREATE POLICY vegetation_plan_limits_policy ON vegetation_plan_limits
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

-- Policy: vegetation_usage_stats
CREATE POLICY vegetation_usage_stats_policy ON vegetation_usage_stats
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

-- Policy: vegetation_usage_log
CREATE POLICY vegetation_usage_log_policy ON vegetation_usage_log
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
-- (Reuses function from migration 001)

-- Triggers for updated_at
DROP TRIGGER IF EXISTS update_vegetation_plan_limits_updated_at ON vegetation_plan_limits;
CREATE TRIGGER update_vegetation_plan_limits_updated_at
    BEFORE UPDATE ON vegetation_plan_limits
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_vegetation_usage_stats_updated_at ON vegetation_usage_stats;
CREATE TRIGGER update_vegetation_usage_stats_updated_at
    BEFORE UPDATE ON vegetation_usage_stats
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- Helper Function: Get or create usage stats for current month
-- =============================================================================
CREATE OR REPLACE FUNCTION get_or_create_usage_stats(
    p_tenant_id TEXT,
    p_year INTEGER DEFAULT EXTRACT(YEAR FROM NOW())::INTEGER,
    p_month INTEGER DEFAULT EXTRACT(MONTH FROM NOW())::INTEGER
) RETURNS UUID AS $$
DECLARE
    v_id UUID;
BEGIN
    -- Try to get existing stats
    SELECT id INTO v_id
    FROM vegetation_usage_stats
    WHERE tenant_id = p_tenant_id
      AND year = p_year
      AND month = p_month;
    
    -- Create if not exists
    IF v_id IS NULL THEN
        INSERT INTO vegetation_usage_stats (tenant_id, year, month)
        VALUES (p_tenant_id, p_year, p_month)
        RETURNING id INTO v_id;
    END IF;
    
    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- End of migration 002
-- =============================================================================

