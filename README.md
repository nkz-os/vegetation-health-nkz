# Vegetation Prime

<div align="center">

**High-performance vegetation intelligence suite for the Nekazari Platform**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.2+-blue.svg)](https://www.typescriptlang.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)

</div>

---

## Overview

**Vegetation Prime** is a production-ready external module for the [Nekazari Platform](https://nekazari.artotxiki.com) that provides advanced vegetation health monitoring using Sentinel-2 L2A satellite imagery. It offers real-time spectral index calculation, historical time series analysis, and high-performance map visualization.

### Key Features

- **Multi-spectral Index Calculation**: NDVI, EVI, SAVI, GNDVI, NDRE, and custom formulas
- **Sentinel-2 L2A Integration**: Automated scene download and processing via Copernicus Data Space Ecosystem
- **Global Scene Cache**: Hybrid caching system to maximize quota savings with shared Copernicus credentials
- **Time Series Analysis**: Historical vegetation trend visualization with interactive timelines
- **High-Performance Visualization**: Deck.gl-based raster rendering with lazy tile caching
- **Asynchronous Processing**: Celery-based job queue for long-running tasks
- **Multi-tenant Architecture**: Row Level Security (RLS) for complete tenant isolation
- **Monetization Ready**: Double-layer limits (volume + frequency) with usage tracking
- **FIWARE Compatible**: Full NGSI-LD integration with Smart Data Models

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Node.js 18+ (for frontend development)
- PostgreSQL with PostGIS (or use the service in docker-compose)
- Redis (or use the service in docker-compose)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/k8-benetis/vegetation-health-nkz.git
cd vegetation-health-nkz

# 2. Start backend services
cd backend
docker-compose up -d

# 3. Verify health check
curl http://localhost:8000/health
# Expected: {"status":"healthy","service":"vegetation-prime"}

# 4. Build frontend
cd ..
npm install
npm run build
```

### Initial Configuration

1. **Configure Copernicus Credentials** (REQUIRED):
   - Credentials are managed centrally by the platform administrator
   - Access: Platform Admin Panel → External API Credentials → Copernicus CDSE
   - The module automatically uses platform-managed credentials
   - See [Platform Credentials Documentation](docs/PLATFORM_CREDENTIALS.md) for details

2. **Install Module for Tenant** (REQUIRED):
   ```sql
   -- Replace 'YOUR_TENANT_ID' with actual tenant ID
   INSERT INTO tenant_installed_modules (tenant_id, module_id, is_enabled, installed_by)
   VALUES ('YOUR_TENANT_ID', 'vegetation-prime', true, 'admin')
   ON CONFLICT (tenant_id, module_id) DO UPDATE SET is_enabled = true;
   ```
   
   **Or use the verification script:**
   ```bash
   # Edit k8s/verify-installation.sql and set tenant_id
   psql $DATABASE_URL -f k8s/verify-installation.sql
   ```

2. **Access Config Page**: Navigate to `/vegetation` in Nekazari Platform
3. **Configure Copernicus Credentials**:
   - Client ID
   - Client Secret
   - Default Index Type (NDVI, EVI, etc.)
4. **Verify Jobs**: Check "Recent Download Jobs" table for download status

**⚠️ IMPORTANT**: If the module redirects to the landing page, verify:
- Module is registered in `marketplace_modules` with `is_active = true`
- Module is installed in `tenant_installed_modules` with `is_enabled = true` for your tenant
- Check browser console for module loading errors

---

## Architecture

### Backend (Python/FastAPI)

- **Framework**: FastAPI with async support
- **Database**: PostgreSQL with PostGIS extension
- **Task Queue**: Celery + Redis
- **Storage**: S3/MinIO abstraction layer with hybrid global cache system
- **Global Cache**: Shared scene storage to maximize Copernicus quota savings
- **Authentication**: JWT (RS256) with Keycloak compatibility
- **Migrations**: PostgreSQL Advisory Locks for safe concurrent migrations

### Frontend (React/Vite)

- **Module Federation**: Remote module using `@originjs/vite-plugin-federation`
  - **React Sharing**: React/ReactDOM/React Router are shared as singletons via Module Federation (required for hooks to work when module renders in host's React tree)
  - **Platform Packages**: `@nekazari/sdk` and `@nekazari/ui-kit` are bundled directly for independence
  - **SDK Integration**: Uses `@nekazari/sdk` with automatic host context resolution via `useAuth()` hook
  - **See**: [Module Development Best Practices](../../nekazari-public/docs/development/MODULE_DEVELOPMENT_BEST_PRACTICES.md) for architecture details
- **UI Framework**: React 18 with TypeScript
- **Map Library**: Deck.gl with Mapbox overlay
- **Styling**: Tailwind CSS + `@nekazari/ui-kit`
- **State Management**: React Context API
- **Web Server**: Nginx with custom configuration for `/modules/vegetation-prime/*` path handling (uses regex location with rewrite to map paths correctly)

---

## ⚠️ CRITICAL REQUIREMENTS FOR EXTERNAL MODULES

**This section documents critical requirements learned from production deployment. Follow these exactly to avoid common pitfalls.**

### 1. Routing - DO NOT USE React Router Internally

**❌ WRONG:**
```tsx
import { Routes, Route, Navigate } from 'react-router-dom';

const MyModule: React.FC = () => {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/config" replace />} />
      <Route path="/config" element={<ConfigPage />} />
    </Routes>
  );
};
```

**✅ CORRECT:**
```tsx
import { useState } from 'react';

type TabType = 'config' | 'analytics';

const MyModule: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabType>('config');
  
  return (
    <div>
      {/* Tab navigation */}
      <button onClick={() => setActiveTab('config')}>Config</button>
      <button onClick={() => setActiveTab('analytics')}>Analytics</button>
      
      {/* Tab content */}
      {activeTab === 'config' && <ConfigPage />}
      {activeTab === 'analytics' && <AnalyticsPage />}
    </div>
  );
};
```

**Why:** The host already provides a `BrowserRouter`. Using `<Routes>`/`<Route>` inside your module creates routing conflicts that cause redirects to the landing page. Use **state-based navigation** (tabs, conditional rendering) instead.

### 2. Module Federation Configuration

**Required in `vite.config.ts`:**

```typescript
federation({
  name: 'your_module_scope',  // Must match registration.sql scope
  filename: 'remoteEntry.js',
  exposes: {
    './App': './src/App.tsx',  // Must match registration.sql exposed_module
  },
  shared: {
    'react': {
      singleton: true,
      requiredVersion: '^18.3.1',
      import: false,  // CRITICAL: Use window.React from host
      shareScope: 'default',
    },
    'react-dom': {
      singleton: true,
      requiredVersion: '^18.3.1',
      import: false,  // CRITICAL: Use window.ReactDOM from host
      shareScope: 'default',
    },
    'react-router-dom': {
      singleton: true,
      requiredVersion: '^6.26.0',
      import: false,  // CRITICAL: Use window.ReactRouterDOM from host
      shareScope: 'default',
    },
  },
})
```

**Required in `build.rollupOptions`:**

```typescript
build: {
  rollupOptions: {
    external: ['react', 'react-dom', 'react-router-dom'],
    output: {
      globals: {
        'react': 'React',
        'react-dom': 'ReactDOM',
        'react-router-dom': 'ReactRouterDOM',
      },
    },
  },
}
```

### 3. Export Format

**✅ REQUIRED:**
```tsx
// src/App.tsx
const MyModule: React.FC = () => {
  return <div>My Module Content</div>;
};

// CRITICAL: Export as default - required for Module Federation
export default MyModule;
```

### 4. Database Registration

**In `k8s/registration.sql`:**

```sql
INSERT INTO marketplace_modules (
    id,
    name,
    display_name,
    remote_entry_url,
    scope,                    -- Must match vite.config.ts federation.name
    exposed_module,           -- Must match vite.config.ts exposes key (e.g., './App')
    route_path,
    is_local,
    is_active,
    -- ... other fields
) VALUES (
    'your-module-id',
    'your-module-name',
    'Your Module Display Name',
    'https://nekazari.artotxiki.com/modules/your-module-name/assets/remoteEntry.js',
    'your_module_scope',      -- Must match vite.config.ts
    './App',                  -- Must match vite.config.ts exposes key
    '/your-module-route',
    false,                    -- External module
    true,                     -- Active
    -- ... other values
);
```

### 5. CSS Isolation

**✅ DO:**
- Use Tailwind CSS with scoped classes
- Use `w-full` instead of `min-h-screen` to avoid layout conflicts
- Keep styles component-scoped

**❌ DON'T:**
- Use global CSS that affects the host
- Use `!important` unless absolutely necessary
- Modify `body` or `html` styles

### 6. Nginx Configuration

**Required in `frontend/nginx.conf`:**

```nginx
location ~ ^/modules/your-module-name/(.*)$ {
    rewrite ^/modules/your-module-name/(.*)$ /$1 break;
    root /usr/share/nginx/html;
    try_files $uri $uri/ /index.html;
}

# CORS headers for Module Federation
add_header Access-Control-Allow-Origin * always;
add_header Access-Control-Allow-Methods "GET, OPTIONS" always;
```

### 7. Common Pitfalls

1. **Routing conflicts**: Never use React Router inside your module
2. **React singleton**: Always use `import: false` for React shared modules
3. **Export format**: Always use `export default` for the main component
4. **Scope mismatch**: Ensure `vite.config.ts` scope matches `registration.sql` scope
5. **Exposed module mismatch**: Ensure `vite.config.ts` exposes key matches `registration.sql` exposed_module
6. **CSS conflicts**: Avoid global styles that affect the host

### 8. Testing Checklist

Before deploying, verify:

- [ ] Module builds without errors: `npm run build`
- [ ] `remoteEntry.js` is generated in `dist/assets/`
- [ ] No React Router usage in module code
- [ ] `export default` is used for main component
- [ ] `vite.config.ts` scope matches `registration.sql` scope
- [ ] `vite.config.ts` exposes key matches `registration.sql` exposed_module
- [ ] Nginx config has correct path rewriting
- [ ] CSS doesn't use global styles that affect host

---

---

## Project Structure

```
vegetation-health-nkz/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI routes
│   │   ├── middleware/       # Auth, limits, service auth
│   │   ├── models/           # SQLAlchemy models
│   │   ├── services/         # Business logic
│   │   └── tasks/            # Celery tasks
│   ├── migrations/           # SQL migrations with RLS
│   ├── scripts/              # Migration runner
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/       # React components
│   │   ├── services/         # API clients
│   │   └── types/            # TypeScript definitions
│   ├── Dockerfile
│   ├── nginx.conf            # Nginx config for /modules/vegetation-prime path
│   └── vite.config.ts
├── k8s/
│   ├── backend-deployment.yaml
│   ├── frontend-deployment.yaml
│   ├── worker-deployment.yaml
│   └── registration.sql       # Module registration in marketplace_modules
├── LICENSE                    # AGPL-3.0
├── manifest.json              # Module metadata
└── README.md
```

---

## API Endpoints

### Jobs
- `POST /api/vegetation/jobs` - Create processing job
- `GET /api/vegetation/jobs/{job_id}` - Get job status
- `GET /api/vegetation/jobs` - List jobs

### Indices
- `GET /api/vegetation/indices` - Get vegetation indices (GeoJSON/XYZ)
- `GET /api/vegetation/timeseries` - Get time series data
- `POST /api/vegetation/calculate` - Calculate index for scene
- `GET /api/vegetation/tiles/{z}/{x}/{y}.png` - Get map tile (lazy caching)

### Configuration
- `GET /api/vegetation/config` - Get tenant configuration
- `POST /api/vegetation/config` - Update tenant configuration

### Usage & Limits
- `GET /api/vegetation/usage/current` - Get current usage statistics
- `POST /api/vegetation/admin/sync-limits` - Sync limits from Core Platform

---

## Security

- **Authentication**: JWT tokens validated against Keycloak JWKS endpoint
- **Authorization**: Tenant ID extracted from `X-Tenant-ID` header
- **Row Level Security**: All database queries filtered by tenant
- **Service Auth**: `X-Service-Auth` header for Core Platform → Module communication
- **Formula Safety**: Custom formulas evaluated using `simpleeval` (no `eval()`)

---

## Monetization

The module implements **double-layer limits** for monetization:

### Volume Limits (Hectares)
- **Monthly limit**: Configurable per plan (default: 10 Ha/month)
- **Daily limit**: Configurable per plan (default: 5 Ha/day)
- **Tracking**: Calculated from job bounds using PostGIS/Shapely

### Frequency Limits (Jobs per Day)
- **Daily jobs limit**: Configurable per plan (default: 5 jobs/day)
- **Per job type**: Separate limits for download/process/calculate
- **Implementation**: Redis atomic counters with daily TTL

### Limit Synchronization
- **Push model**: Core Platform pushes limits via `POST /api/vegetation/admin/sync-limits`
- **Fallback**: Safe defaults if limits not synced
- **Validation**: Limits checked BEFORE job creation (HTTP 429 if exceeded)

---

## Development

### Backend Setup

```bash
cd backend
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL="postgresql://user:pass@localhost:5432/nekazari"
export CELERY_BROKER_URL="redis://localhost:6379/0"
export REDIS_CACHE_URL="redis://localhost:6379/1"

# Run migrations
python scripts/run_migrations.py

# Start FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start Celery worker (separate terminal)
celery -A app.celery_app worker --loglevel=info
```

### Frontend Setup

```bash
npm install
npm run dev        # Development server
npm run build      # Production build
npm run typecheck  # TypeScript validation
```

---

## Docker Deployment

### Using Pre-built Images (Recommended)

Images are automatically built and published to **GitHub Container Registry (GHCR)**:
- **On every push to `main`**: Builds `latest` tag (for continuous deployment)
- **On release tags (`v*.*.*`)**: Builds versioned tags (e.g., `v1.0.0`, `v1.0`, `v1`)

```bash
# Backend
docker run -d \
  --name vegetation-prime-backend \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/nekazari" \
  -e MODULE_MANAGEMENT_KEY="your-secret-key" \
  -e CELERY_BROKER_URL="redis://redis:6379/0" \
  -e REDIS_CACHE_URL="redis://redis:6379/1" \
  ghcr.io/k8-benetis/vegetation-health-nkz/vegetation-prime-backend:v1.0.0

# Frontend
docker run -d \
  --name vegetation-prime-frontend \
  -p 80:80 \
  ghcr.io/k8-benetis/vegetation-health-nkz/vegetation-prime-frontend:v1.0.0
```

**Available tags**: `latest` (main branch), `v1.0.0` (releases), version tags follow semantic versioning

### Building and Pushing Locally (With Real-time Logs)

For development and testing, you can build locally and see logs in real-time:

```bash
# Build and push to GHCR (interactive, shows all logs)
./scripts/build-and-push.sh v1.0.0

# Or just build locally for testing (no push)
./scripts/build-local.sh local
```

**Benefits of local builds:**
- See build logs in real-time
- Test images before pushing
- Debug build issues easily
- Faster iteration during development

**When to use each:**
- **Local builds**: Development, testing, debugging, custom versions
- **GitHub Actions**: Production releases, automated CI/CD, multi-arch builds

See [Docker Images Documentation](docs/DOCKER_IMAGES.md) for details.

### Docker Compose (Full Stack)

```bash
cd backend
docker-compose up -d
```

---

## Kubernetes Deployment

### Prerequisites

- Access to Nekazari Platform Kubernetes cluster
- `kubectl` configured with cluster access
- GitHub Container Registry (GHCR) credentials configured as `ghcr-secret` in namespace

### Deployment Steps

1. **Build and Push Images** (if not using CI/CD):
   ```bash
   # Build locally
   docker build -f frontend/Dockerfile -t ghcr.io/k8-benetis/vegetation-health-nkz/vegetation-prime-frontend:v1.0.0 .
   docker build -f backend/Dockerfile -t ghcr.io/k8-benetis/vegetation-health-nkz/vegetation-prime-backend:v1.0.0 ./backend
   
   # Push to GHCR
   docker push ghcr.io/k8-benetis/vegetation-health-nkz/vegetation-prime-frontend:v1.0.0
   docker push ghcr.io/k8-benetis/vegetation-health-nkz/vegetation-prime-backend:v1.0.0
   ```

2. **Register Module**: Execute `k8s/registration.sql` in Core Platform database:
   ```bash
   # From the module repository
   psql $DATABASE_URL -f k8s/registration.sql
   ```

3. **Deploy Kubernetes Resources**:
   ```bash
   # Apply backend and worker (no separate frontend deployment — IIFE bundle is served from MinIO)
   kubectl apply -f k8s/backend-deployment.yaml
   kubectl apply -f k8s/worker-deployment.yaml
   ```

4. **Ingress** (Core Platform — `nkz` repo):
   - **Only** add route `/api/vegetation` → `vegetation-prime-api-service:8000`.
   - **Do NOT** add a route `/modules/vegetation-prime` to any per-module service. The platform serves the module IIFE from MinIO via `frontend-static` at `https://<frontend-domain>/modules/vegetation-prime/nkz-module.js`. A per-module ingress for `/modules/vegetation-prime` would intercept that URL and return HTML (SPA fallback), breaking registration.

5. **Deploy module bundle to MinIO** (after `pnpm run build`):
   - Upload `dist/nkz-module.js` to bucket `nekazari-frontend`, key `modules/vegetation-prime/nkz-module.js` (via `mc` or S3 API). Set `marketplace_modules.remote_entry_url = '/modules/vegetation-prime/nkz-module.js'`.

6. **Verify Deployment**:
   ```bash
   kubectl get pods -n nekazari | grep vegetation-prime
   curl -sI "https://nekazari.robotika.cloud/modules/vegetation-prime/nkz-module.js"
   # Must return 200 and Content-Type: text/javascript (not text/html)
   ```

### Important Notes

- **Image Versions**: Deployments use versioned tags (e.g., `v1.0.0`) instead of `latest` for stability
- **Environment Variables**: Secrets (including `MODULE_MANAGEMENT_KEY`, `DATABASE_URL`, etc.) are managed by Core Platform and injected via ConfigMaps/Secrets
- **Image Pull Policy**: Set to `Always` to ensure latest images are pulled (consider using specific versions in production)

---

## Database Schema

The module creates the following tables with Row Level Security (RLS):

- `vegetation_config` - Tenant configuration
- `vegetation_jobs` - Processing job tracking
- `vegetation_scenes` - Sentinel-2 scene metadata
- `vegetation_indices_cache` - Pre-calculated index values
- `vegetation_custom_formulas` - User-defined formulas
- `vegetation_plan_limits` - Plan limits (synced from Core)
- `vegetation_usage_stats` - Monthly aggregated usage
- `vegetation_usage_log` - Detailed usage log

All tables include:
- `tenant_id` for multi-tenancy
- `created_at` and `updated_at` timestamps
- RLS policies for tenant isolation

---

## Configuration

### Environment Variables

#### Required (Injected by Core Platform in Production)
- `MODULE_MANAGEMENT_KEY` - Service-to-service authentication key
- `DATABASE_URL` - PostgreSQL connection string
- `CELERY_BROKER_URL` - Redis URL for Celery
- `REDIS_CACHE_URL` - Redis URL for tile cache
- `JWT_ISSUER` - Keycloak issuer URL
- `JWKS_URL` - Keycloak JWKS endpoint

#### Optional (With Defaults)
- `LOG_LEVEL` - Logging level (default: `INFO`)
- `DEFAULT_MONTHLY_HA_LIMIT` - Fallback monthly limit (default: `10.0`)
- `DEFAULT_DAILY_HA_LIMIT` - Fallback daily limit (default: `5.0`)
- `S3_ENDPOINT_URL` - S3/MinIO endpoint (if not AWS)
- `S3_BUCKET` - Default bucket name (default: `vegetation-prime`)

---

## License

This project is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0).

See [LICENSE](LICENSE) file for details.

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## Documentation

- **[User Manual](docs/USER_MANUAL.md)**: Complete user guide covering all features, pages, and workflows
- **[Global Cache System](docs/GLOBAL_CACHE_SYSTEM.md)**: Hybrid caching architecture for quota optimization (includes migration instructions)
- **[Platform Credentials](docs/PLATFORM_CREDENTIALS.md)**: Centralized credential management
- **[Build Strategy](docs/BUILD_STRATEGY.md)**: Docker image build and deployment strategy

### Database Migrations

After deploying a new version, ensure all migrations are applied:

```bash
# Option 1: Using migration script (recommended)
kubectl exec -it <backend-pod> -n nekazari -- python /app/scripts/run_migrations.py

# Option 2: Manual execution
kubectl exec -it <backend-pod> -n nekazari -- psql $DATABASE_URL -f /app/migrations/003_create_global_scene_cache.sql
```

See [Global Cache System Documentation](docs/GLOBAL_CACHE_SYSTEM.md#migration) for detailed migration instructions.

---

## Support

For issues, questions, or contributions:
- **Issues**: [GitHub Issues](https://github.com/k8-benetis/vegetation-health-nkz/issues)
- **Email**: nekazari@artotxiki.com

---

## Acknowledgments

- **Nekazari Platform** - For the modular architecture and SDK
- **Copernicus Data Space Ecosystem** - For Sentinel-2 L2A data access
- **FIWARE** - For Smart Data Models and NGSI-LD standards
- **Deck.gl** - For high-performance map visualization

---

<div align="center">

**Made for the Nekazari Platform**

[Website](https://nekazari.artotxiki.com) • [Documentation](https://nekazari.artotxiki.com) • [License](LICENSE)

</div>
