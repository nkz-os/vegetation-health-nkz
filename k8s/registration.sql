-- =============================================================================
-- K8s Module Registration SQL
-- =============================================================================
-- Registers the Vegetation Prime module in the Core Platform database
-- for Kubernetes deployment.
--
-- USAGE:
-- Execute this SQL in the Core Platform database (nekazari_core schema)
-- after deploying the module to Kubernetes.
--
-- PREREQUISITES:
-- - Module backend deployed to K8s (service: vegetation-prime-api)
-- - Module frontend deployed to K8s (service: vegetation-prime-frontend)
-- - Module accessible via ingress or service URLs
-- =============================================================================

-- Insert module registration
INSERT INTO marketplace_modules (
    id,
    name,
    display_name,
    description,
    remote_entry_url,
    scope,
    exposed_module,
    version,
    author,
    category,
    icon_url,
    route_path,
    label,
    module_type,
    required_plan_type,
    pricing_tier,
    is_local,
    is_active,
    required_roles,
    metadata
) VALUES (
    'vegetation-prime',                                                              -- Module ID
    'vegetation-prime',                                                              -- Internal name
    'Vegetation Prime',                                                              -- Display name
    'High-performance vegetation intelligence suite for Sentinel-2 analysis. Provides NDVI, EVI, SAVI, GNDVI, NDRE indices with real-time tile serving and historical time series analysis.',
    '/modules/vegetation-prime/assets/remoteEntry.js',                               -- Remote entry URL (relative to host)
    'vegetation_prime_module',                                                       -- Module Federation scope (must match vite.config.ts)
    './App',                                                                         -- Exposed module path (must match vite.config.ts)
    '1.50.0',                                                                         -- Version
    'Nekazari Team',                                                                 -- Author
    'analytics',                                                                     -- Category
    NULL,                                                                            -- Icon URL (optional)
    '/vegetation',                                                                   -- Frontend route path
    'Vegetation',                                                                    -- Menu label
    'ADDON_PAID',                                                                    -- Module type
    'premium',                                                                       -- Required plan type
    'PAID',                                                                          -- Pricing tier
    false,                                                                           -- Is local (external module)
    true,                                                                            -- Is active
    ARRAY['Farmer', 'TenantAdmin', 'PlatformAdmin'],                                -- Required roles
    '{
        "icon": "🌱",
        "color": "#10B981",
        "shortDescription": "Advanced vegetation health monitoring and analysis",
        "features": [
            "Multi-spectral index calculation (NDVI, EVI, SAVI, GNDVI, NDRE)",
            "Custom formula engine",
            "Sentinel-2 L2A integration",
            "Time series analysis",
            "FIWARE NGSI-LD integration",
            "High-performance raster visualization"
        ],
        "backend_services": ["vegetation-prime-api"],
        "external_dependencies": ["Copernicus Data Space Ecosystem"],
        "contextPanel": {
            "description": "Analyze vegetation health using Sentinel-2 satellite imagery",
            "instructions": "Select a parcel and create a job to analyze vegetation indices",
            "entityTypes": ["AgriParcel"]
        },
        "permissions": {
            "api_access": true,
            "external_requests": true,
            "storage": true,
            "geospatial": true
        }
    }'::jsonb
) ON CONFLICT (id) DO UPDATE SET
    version = EXCLUDED.version,
    description = EXCLUDED.description,
    remote_entry_url = EXCLUDED.remote_entry_url,
    scope = EXCLUDED.scope,
    exposed_module = EXCLUDED.exposed_module,
    route_path = EXCLUDED.route_path,
    label = EXCLUDED.label,
    module_type = EXCLUDED.module_type,
    required_plan_type = EXCLUDED.required_plan_type,
    pricing_tier = EXCLUDED.pricing_tier,
    metadata = EXCLUDED.metadata,
    updated_at = NOW();

-- =============================================================================
-- NOTES:
-- =============================================================================
-- 1. REMOTE_ENTRY_URL: Must be the public URL accessible through ingress
--    Format: https://nekazari.artotxiki.com/modules/{module-id}/assets/remoteEntry.js
--
-- 2. SCOPE: Must match the 'name' field in vite.config.ts federation plugin
--    Current: 'vegetation_prime_module'
--
-- 3. EXPOSED_MODULE: Must match the key in vite.config.ts federation exposes
--    Current: './App'
--
-- 4. MODULE_TYPE: 
--    - 'ADDON_FREE': Free for all tenants
--    - 'ADDON_PAID': Requires subscription (monetization enabled)
--    - 'ENTERPRISE': Enterprise-only features
--
-- 5. After registration, the Core Platform will:
--    - Load the module frontend via Module Federation from remote_entry_url
--    - Display the module in the marketplace/admin panel
--
-- 6. To activate for a specific tenant:
--    INSERT INTO tenant_installed_modules (tenant_id, module_id, is_enabled)
--    VALUES ('your-tenant-id', 'vegetation-prime', true)
--    ON CONFLICT (tenant_id, module_id) DO UPDATE SET is_enabled = true;
-- =============================================================================



