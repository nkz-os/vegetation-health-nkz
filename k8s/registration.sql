-- =============================================================================
-- K8s Module Registration SQL Template
-- =============================================================================
-- USAGE: Replace placeholders like {{VERSION}} and {{ENTRY_PATH}}
-- =============================================================================

INSERT INTO marketplace_modules (
    id, name, display_name, description, remote_entry_url, 
    scope, exposed_module, version, author, category, 
    route_path, label, module_type, required_plan_type, 
    pricing_tier, is_local, is_active, required_roles, metadata
) VALUES (
    'vegetation-prime',
    'vegetation-prime',
    'Vegetation Prime',
    'High-performance vegetation intelligence suite for Sentinel-2 analysis.',
    '/modules/vegetation-prime/assets/remoteEntry.js', -- Internal relative path
    'vegetation_prime_module',
    './App',
    '{{MODULE_VERSION}}',
    'Nekazari Team',
    'analytics',
    '/vegetation',
    'Vegetation',
    'ADDON_PAID',
    'premium',
    'PAID',
    false,
    true,
    ARRAY['Farmer', 'TenantAdmin', 'PlatformAdmin'],
    '{
        "icon": "🌱",
        "color": "#10B981",
        "backend_services": ["vegetation-prime-api"],
        "contextPanel": {
            "entityTypes": ["AgriParcel"]
        }
    }'::jsonb
) ON CONFLICT (id) DO UPDATE SET
    version = EXCLUDED.version,
    remote_entry_url = EXCLUDED.remote_entry_url,
    updated_at = NOW();
