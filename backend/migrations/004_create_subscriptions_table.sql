-- Migration 004: Create Vegetation Subscriptions Table

CREATE TABLE IF NOT EXISTS vegetation_subscriptions (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Tenant Mixin
    tenant_id TEXT NOT NULL,
    
    -- Entity Reference
    entity_id VARCHAR NOT NULL,
    entity_type VARCHAR NOT NULL DEFAULT 'AgriParcel',
    
    -- Geometry (PostGIS)
    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    
    -- Configuration
    start_date DATE NOT NULL,
    index_types VARCHAR[],
    frequency VARCHAR DEFAULT 'weekly',
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    status VARCHAR DEFAULT 'created',
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    last_error VARCHAR
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_vegetation_subscriptions_tenant_id ON vegetation_subscriptions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_subscriptions_entity_id ON vegetation_subscriptions(entity_id);
CREATE INDEX IF NOT EXISTS idx_vegetation_subscriptions_status ON vegetation_subscriptions(status);
