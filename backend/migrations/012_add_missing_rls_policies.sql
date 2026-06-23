-- Add RLS policies for tables created without proper tenant isolation
-- Requires app.current_tenant to be set (see database.py)

-- vegetation_subscriptions: enable RLS
ALTER TABLE vegetation_subscriptions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'vegetation_subscriptions' AND policyname = 'tenant_isolation_subscriptions'
    ) THEN
        CREATE POLICY tenant_isolation_subscriptions ON vegetation_subscriptions
            USING (tenant_id = current_setting('app.current_tenant')::text);
    END IF;
END $$;

-- vegetation_crop_seasons: enable RLS
ALTER TABLE vegetation_crop_seasons ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'vegetation_crop_seasons' AND policyname = 'tenant_isolation_crop_seasons'
    ) THEN
        CREATE POLICY tenant_isolation_crop_seasons ON vegetation_crop_seasons
            USING (tenant_id = current_setting('app.current_tenant')::text);
    END IF;
END $$;
