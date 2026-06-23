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

-- vegetation_monitoring_periods (was crop_seasons pre-migration 008): enable RLS
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'vegetation_monitoring_periods'
    ) THEN
        EXECUTE 'ALTER TABLE vegetation_monitoring_periods ENABLE ROW LEVEL SECURITY';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'vegetation_monitoring_periods' AND policyname = 'tenant_isolation_monitoring_periods'
        ) THEN
            EXECUTE 'CREATE POLICY tenant_isolation_monitoring_periods ON vegetation_monitoring_periods
                USING (tenant_id = current_setting(''app.current_tenant'')::text)';
        END IF;
    END IF;
END $$;

-- Also check legacy name (crop_seasons) in case migration 008 wasn't applied
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'vegetation_crop_seasons'
    ) THEN
        EXECUTE 'ALTER TABLE vegetation_crop_seasons ENABLE ROW LEVEL SECURITY';
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'vegetation_crop_seasons' AND policyname = 'tenant_isolation_crop_seasons'
        ) THEN
            EXECUTE 'CREATE POLICY tenant_isolation_crop_seasons ON vegetation_crop_seasons
                USING (tenant_id = current_setting(''app.current_tenant'')::text)';
        END IF;
    END IF;
END $$;
