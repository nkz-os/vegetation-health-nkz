-- =============================================================================
-- Verify and Install Vegetation Prime Module for Tenant
-- =============================================================================
-- This script verifies if the module is installed for a tenant and installs it if needed.
-- 
-- USAGE:
-- Replace 'YOUR_TENANT_ID' with the actual tenant ID
-- Execute: psql $DATABASE_URL -f k8s/verify-installation.sql
-- =============================================================================

-- Set tenant ID (REPLACE WITH ACTUAL TENANT ID)
\set tenant_id 'YOUR_TENANT_ID'

-- 1. Verify module is registered in marketplace_modules
SELECT 
    'Module Registration Check' as check_type,
    CASE 
        WHEN EXISTS (SELECT 1 FROM marketplace_modules WHERE id = 'vegetation-prime' AND is_active = true)
        THEN '✅ Module is registered and active'
        ELSE '❌ Module is NOT registered or NOT active'
    END as status;

-- 2. Verify module is installed for tenant
SELECT 
    'Tenant Installation Check' as check_type,
    CASE 
        WHEN EXISTS (
            SELECT 1 
            FROM tenant_installed_modules tim
            INNER JOIN marketplace_modules mm ON tim.module_id = mm.id
            WHERE tim.tenant_id = :'tenant_id'
                AND tim.module_id = 'vegetation-prime'
                AND tim.is_enabled = true
                AND mm.is_active = true
        )
        THEN '✅ Module is installed and enabled for tenant'
        ELSE '❌ Module is NOT installed or NOT enabled for tenant'
    END as status;

-- 3. Show current installation status
SELECT 
    tim.tenant_id,
    tim.module_id,
    mm.display_name,
    tim.is_enabled,
    mm.is_active as module_is_active,
    mm.route_path,
    mm.remote_entry_url
FROM tenant_installed_modules tim
INNER JOIN marketplace_modules mm ON tim.module_id = mm.id
WHERE tim.module_id = 'vegetation-prime'
    AND tim.tenant_id = :'tenant_id';

-- 4. Install module for tenant if not installed
-- Uncomment and execute if module needs to be installed:
/*
INSERT INTO tenant_installed_modules (tenant_id, module_id, is_enabled, installed_by)
VALUES (:'tenant_id', 'vegetation-prime', true, 'admin')
ON CONFLICT (tenant_id, module_id) DO UPDATE SET is_enabled = true, updated_at = NOW();
*/

-- 5. Verify installation after running INSERT (if needed)
SELECT 
    'Post-Installation Verification' as check_type,
    CASE 
        WHEN EXISTS (
            SELECT 1 
            FROM tenant_installed_modules tim
            WHERE tim.tenant_id = :'tenant_id'
                AND tim.module_id = 'vegetation-prime'
                AND tim.is_enabled = true
        )
        THEN '✅ Module is now installed and enabled'
        ELSE '❌ Installation failed - check logs'
    END as status;

















