-- 007_add_crop_seasons.sql
-- Crop seasons: links a parcel (AgriParcel entity) to a crop type + date range.
-- A parcel can have multiple seasons (one active, plus historical).

CREATE TABLE IF NOT EXISTS vegetation_crop_seasons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    crop_type TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    label TEXT,
    monitoring_enabled BOOLEAN NOT NULL DEFAULT false,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_entity_crop_period UNIQUE (tenant_id, entity_id, crop_type, start_date)
);

CREATE INDEX idx_crop_seasons_tenant_entity ON vegetation_crop_seasons(tenant_id, entity_id);
