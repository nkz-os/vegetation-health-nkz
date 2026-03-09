# Vegetation Prime (NKZ Module)

<div align="center">

**SOTA Vegetation Intelligence Suite for Nekazari Platform**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.2-blue.svg)](https://www.typescriptlang.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-2.0-green.svg)](https://fastapi.tiangolo.com/)
[![GitOps](https://img.shields.io/badge/Ops-ArgoCD-orange.svg)](https://argoproj.github.io/cd/)

</div>

---

## 🚀 Overview

**Vegetation Prime** is the flagship analytics module for the [Nekazari Platform](https://robotika.cloud). It provides high-performance vegetation health monitoring using Sentinel-2 L2A satellite imagery, powered by an internal COG-to-Tile rendering engine.

### Key Features

- **Internal Rendering Engine**: Built-in XYZ tile server using `rio-tiler`. No external dependencies like TiTiler.
- **Sentinel-2 L2A Integration**: Automated scene discovery and processing via Copernicus Data Space.
- **Spectral Indices**: Real-time calculation of NDVI, EVI, SAVI, GNDVI, NDRE, and custom formulas.
- **Smart Timeline**: Historical analysis with cloud-coverage filtering and index trends.
- **FIWARE Digital Twins**: Full integration with Orion-LD for managing `AgriParcel` entities.
- **SOTA Architecture**: SOLID backend design and IIFE-based frontend injection.

---

## 🏗️ Architecture

### Backend (Python/FastAPI)
- **SOLID Structure**: Decoupled routers for `jobs`, `tiles`, `entities`, and `analytics`.
- **GIS Core**: `rasterio` and `rio-tiler` for efficient Cloud-Optimized GeoTIFF (COG) processing.
- **Async Workers**: Celery + Redis for heavy satellite data processing.
- **Storage**: MinIO (S3 compatible) with tenant-isolated buckets.
- **Security**: Non-root container execution (`appuser`) and JWT (RS256) validation.

### Frontend (React/TypeScript)
- **IIFE Bundle**: Single-file bundle injection via `window.__NKZ__.register()`.
- **CesiumJS Integration**: Native 3D visualization using `UrlTemplateImageryProvider` pointing to the internal tile API.
- **Shared SDK**: Leverages `@nekazari/sdk` and `@nekazari/ui-kit` for UI consistency.
- **Navigation**: State-based navigation (no internal React Router) to prevent host conflicts.

---

## 🛠️ Deployment (GitOps)

This module follows the **GitOps paradigm** managed by ArgoCD.

### Automatic Sync
1. Push changes to the `main` branch.
2. ArgoCD detects changes in `gitops/modules/vegetation-prime.yaml` (in the `nkz` core repo).
3. The cluster automatically synchronizes state, pulling the latest images and applying manifests.

### Manual Build (Local Testing)
```bash
# Build the IIFE frontend
npm run build

# Start local backend stack
cd backend
docker-compose up -d
```

---

## 📡 API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/vegetation/tiles/{job_id}/{z}/{x}/{y}.png` | GET | Internal XYZ tile renderer (Authenticated) |
| `/api/vegetation/jobs` | POST | Create Sentinel-2 download or calculation job |
| `/api/vegetation/entities/{id}/scenes/available` | GET | Fetch available dates for the Smart Timeline |
| `/api/vegetation/config` | GET/POST | Manage tenant-specific Copernicus credentials |

---

## 🔒 Security & Standards

- **Free Software**: Licensed under **AGPL-3.0**.
- **No Hardcoding**: Credentials managed via K8s Secrets and SOPS.
- **Data Integrity**: NGSI-LD compliant data models.
- **Isolation**: Strict multi-tenancy at API, Database (RLS), and Storage layers.

---
<div align="center">
Made with ❤️ by the Nekazari Team
</div>
