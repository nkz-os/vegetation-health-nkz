-- 018_monitoring_state.sql
-- Why a subscription is paused, and which modules ask for continuous monitoring
-- of a parcel. Process state, not an observed series.
-- Expand-only and idempotent.

ALTER TABLE vegetation_subscriptions ADD COLUMN IF NOT EXISTS paused_reason TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'vegetation_subscriptions_paused_reason_check'
          AND conrelid = 'vegetation_subscriptions'::regclass
    ) THEN
        ALTER TABLE vegetation_subscriptions
            ADD CONSTRAINT vegetation_subscriptions_paused_reason_check
            CHECK (paused_reason IN ('harvest', 'user'));
    END IF;
END $$;

-- Until now only a person paused a subscription (deactivation or PATCH).
UPDATE vegetation_subscriptions
   SET paused_reason = 'user'
 WHERE is_active = false AND paused_reason IS NULL;

CREATE TABLE IF NOT EXISTS vegetation_continuous_requests (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    module_id  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ux_vegetation_continuous_requests UNIQUE (tenant_id, entity_id, module_id)
);

ALTER TABLE vegetation_continuous_requests ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'vegetation_continuous_requests' AND policyname = 'tenant_isolation_continuous_requests'
    ) THEN
        CREATE POLICY tenant_isolation_continuous_requests ON vegetation_continuous_requests
            USING (tenant_id = current_setting('app.current_tenant')::text);
    END IF;
END $$;
