# Changelog

All notable changes to the Vegetation Prime module will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-04-30

### Added
- `NKZ_AUTH_INJECTION` listener (`useMobileAuth` hook) for nkz-mobile WebView auth
- Mobile Bearer token fallback in API client (dual cookie + Bearer mode)
- `vegetation_crop_seasons` table + CRUD API: link parcel to crop type + date range
- `cropSeason` i18n namespace across all 6 locales (es, en, eu, ca, fr, pt)
- Catalan (ca), French (fr), Portuguese (pt) locale files

### Changed
- **Setup wizard**: replaced 3-step wizard with single modal (crop type + start date + monitoring toggle)
- **Timeline**: now sparse event-based from availability API (`GET scenes/available`), tick marks colored by NDVI value, tooltips on hover
- **Calculation history**: replaced card grid + bar charts with flat sortable HTML table
- **Layer control**: `w-80` → `w-full max-w-[320px]` for responsive mobile viewports

### Fixed
- Dockerfile `COPY` paths corrected for `backend/` build context (was referencing non-existent `backend/backend/`)
- SQLAlchemy column type mismatches: `Text` → `DateTime`/`Boolean`/`Numeric` in 4 model fields
- Replaced all `print()` calls with structured `logger.info/warning/error` in scheduler and subscriptions
- Replaced DaisyUI `toggle` classes (undefined dependency) with pure Tailwind CSS toggle component
- ~28 hardcoded Spanish UI strings migrated to `t()` i18n calls

### Removed
- Dead code: `src/i18n/index.ts` (orphaned i18next instance, never imported)
- Unnecessary re-export `viewerSlots` from `App.tsx`
- Weather/Eguraldia tab and all `weather.*` i18n keys (belongs to IoT/DataHub domain)
- SAMI (Sentinel-1 SAR) index from types, legend, and UI (no pipeline implemented)
- Unused `@deck.gl/*` dependencies

## [1.0.0] - 2025-01-XX

### Added
- Initial release of Vegetation Prime module
- Multi-spectral index calculation (NDVI, EVI, SAVI, GNDVI, NDRE)
- Custom formula engine with secure evaluation
- Sentinel-2 L2A integration via Copernicus Data Space Ecosystem
- Asynchronous job processing with Celery + Redis
- High-performance tile serving with lazy caching
- Time series analysis and visualization
- FIWARE NGSI-LD integration
- Multi-tenant architecture with Row Level Security (RLS)
- Monetization system with double-layer limits (volume + frequency)
- Usage tracking and statistics
- Service-to-service authentication (`X-Service-Auth`)
- PostgreSQL Advisory Locks for safe concurrent migrations
- Docker Compose setup for local development
- Kubernetes deployment manifests
- Module Federation integration for Nekazari Platform
- Frontend components: ConfigPage, AnalyticsPage, TimelineWidget, VegetationLayer
- Comprehensive API documentation
- Database migrations with rollback scripts

### Security
- JWT authentication with Keycloak compatibility
- Row Level Security (RLS) on all database tables
- Service authentication for Core Platform communication
- Secure formula evaluation (no `eval()`)
- Constant-time string comparison for API key validation

### Performance
- Lazy tile caching with Redis (50-200ms → <10ms)
- Vectorized NumPy operations for colormap application
- Asynchronous FastAPI endpoints
- Connection pooling for database and Redis
- Optimized PostGIS queries

---

## [Unreleased]

### Planned
- AI-powered vegetation trend prediction (ARIMA/Prophet)
- Additional vegetation indices (NDWI, NDMI, etc.)
- Batch processing for multiple parcels
- Export functionality (PDF reports, CSV data)
- Advanced analytics dashboard
- Webhook support for job completion notifications
