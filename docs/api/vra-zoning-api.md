---
title: "VRA Zoning API"
description: "API contract for Variable Rate Application management zones -- consumable by GIS, machinery guidance, and external modules."
---

# VRA Zoning API

## Overview

The Vegetation Prime module generates **management zones** (also called VRA zones) via K-means clustering of vegetation index rasters (typically NDVI). Each zone represents an area of statistically similar crop vigor, enabling variable-rate application of fertilizer, pesticide, or seed.

The zoning pipeline works as follows:

1. A `calculate_index` job with parameter `index_type: VRA_ZONES` is created via `POST /api/vegetation/jobs`.
2. The background worker fetches the latest NDVI raster from object storage, runs K-means clustering (scipy `kmeans2`), and vectorizes the cluster labels into GeoJSON polygon features.
3. Each zone is also upserted as an `AgriManagementZone` NGSI-LD entity in the Context Broker (Orion-LD), linked to the parent `AgriParcel` via `refAgriParcel` relationship.
4. The GeoJSON result is persisted in the job's result payload and can be retrieved via the zoning GeoJSON endpoint or downloaded in multiple export formats.

**Key characteristics:**

- **Algorithm:** K-Means (`scipy.cluster.vq.kmeans2`) on whitened NDVI pixel values
- **Default zones:** 3 (configurable via `parameters.n_zones`)
- **Coordinate system:** WGS 84 (EPSG:4326) -- GeoJSON `[longitude, latitude]`
- **Raster source:** Latest COG (Cloud-Optimized GeoTIFF) from vegetation index cache
- **Data flow:** Object storage -> rasterio read -> cluster -> rasterio.features.shapes vectorize -> GeoJSON

---

## Endpoints

### Base URL

All endpoints are served by the Vegetation Prime backend. In production:

- Internal cluster: `http://vegetation-prime-service:5000` (or the port configured in the module's K8s service)
- External via api-gateway: `https://nkz.robotika.cloud/api/vegetation/...`

### 1. Retrieve Latest Zoning GeoJSON

```
GET /api/vegetation/jobs/zoning/{parcel_id}/geojson
```

Returns the **complete GeoJSON FeatureCollection** from the most recent completed `VRA_ZONES` job for the given parcel. This is the primary endpoint for programmatic consumption.

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `parcel_id` | string | The NGSI-LD entity ID of the AgriParcel (e.g. `urn:ngsi-ld:AgriParcel:abc123`) |

**Headers:**

| Header | Required | Value |
|--------|----------|-------|
| `Authorization` | Yes* | `Bearer <JWT>` |
| `X-Tenant-ID` | Yes | Tenant identifier (e.g. `tenant-slug`) |

\* The `nkz_token` httpOnly cookie is also accepted as a fallback (see Authentication section).

**Success response (200):**

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "zone_id": 0,
        "cluster_id": 0,
        "zone_class": "low",
        "mean_value": 0.2145,
        "area_ha": 2.85,
        "prescription_rate": 0.96
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon, lat], ...]]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "zone_id": 1,
        "cluster_id": 1,
        "zone_class": "medium",
        "mean_value": 0.4521,
        "area_ha": 4.12,
        "prescription_rate": 1.08
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon, lat], ...]]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "zone_id": 2,
        "cluster_id": 2,
        "zone_class": "high",
        "mean_value": 0.6834,
        "area_ha": 3.51,
        "prescription_rate": 1.19
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon, lat], ...]]
      }
    }
  ]
}
```

**Error responses:**

| Status | Meaning |
|--------|---------|
| `404` | No zoning data available (no completed VRA_ZONES job, or result has no geojson) |
| `401` | Missing or invalid authentication |
| `400` | Missing `X-Tenant-ID` header |

### 2. Export Prescription Map as GeoJSON (download)

```
GET /api/vegetation/export/{parcel_id}/geojson
```

Downloads the zoning result as a standalone GeoJSON file with metadata. Returns `Content-Disposition: attachment`.

**Headers, auth, path params:** Same as endpoint 1.

**Response:** `application/geo+json` with filename `prescription_{parcel_id}.geojson`

The response body is a GeoJSON FeatureCollection identical in structure to endpoint 1, with additional top-level metadata fields:

```json
{
  "type": "FeatureCollection",
  "features": [...],
  "metadata": {},
  "generated_at": "2026-04-29T12:00:00.000Z",
  "generator": "Nekazari Vegetation Prime"
}
```

### 3. Export Prescription Map as Shapefile (download)

```
GET /api/vegetation/export/{parcel_id}/shapefile
```

Downloads the zoning result as a **zipped Shapefile** (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`) compatible with QGIS, ArcGIS, and farm management software.

**Response:** `application/zip` with filename `prescription_{parcel_id}.zip`

**Notes:**

- CRS: EPSG:4326 (WGS 84)
- Property schema auto-detected from first feature's properties
- Requires `fiona` and `shapely` on the backend (returns `501` if unavailable)
- Boolean values are cast to string (Shapefile limitation)

### 4. Export Prescription Map as CSV (download)

```
GET /api/vegetation/export/{parcel_id}/csv
```

Downloads the zoning result as a flat CSV with WKT geometry.

**Response:** `text/csv` with filename `prescription_{parcel_id}.csv`

**Columns:**

| Column | Description |
|--------|-------------|
| `id` | Sequential feature index (1-based) |
| `cluster_id` | Zone cluster identifier (0, 1, 2...) |
| `geometry_wkt` | Polygon geometry in WKT format (e.g. `POLYGON ((lon lat, ...))`) |

Additional property columns may appear if features carry extra properties.

### 5. Job Result Download (raster/statistics)

```
GET /api/vegetation/jobs/{job_id}/download?format={format}
```

Downloads the raw job result. For zoning jobs this can return:

- `format=geotiff` -- the source NDVI raster used for clustering (COG, `image/tiff`)
- `format=png` -- normalized PNG rendering of the raster (`image/png`)
- `format=csv` -- job statistics as key-value pairs (`text/csv`)

This endpoint requires the specific job UUID, not the parcel ID.

### 6. List Jobs for a Parcel

```
GET /api/vegetation/jobs?entity_id={parcel_id}&status=completed
```

Lists completed jobs for a parcel. Filter by `status=completed` to find the VRA_ZONES job IDs.

**Response:**

```json
{
  "jobs": [
    {
      "id": "uuid",
      "job_type": "calculate_index",
      "entity_id": "urn:ngsi-ld:AgriParcel:abc123",
      "status": "completed",
      "parameters": { "index_type": "VRA_ZONES", "n_zones": 3 },
      "result": { "geojson": {...}, "statistics": {...} },
      "created_at": "2026-04-29T10:00:00Z",
      "updated_at": "2026-04-29T10:05:00Z"
    }
  ],
  "total": 1
}
```

### 7. Trigger Zoning Job

```
POST /api/vegetation/jobs
```

Creates a new vegetation calculation job. To trigger VRA zoning, set `job_type` to `calculate_index` and include `"index_type": "VRA_ZONES"` in parameters.

**Request body:**

```json
{
  "job_type": "calculate_index",
  "entity_id": "urn:ngsi-ld:AgriParcel:abc123",
  "entity_type": "AgriParcel",
  "parameters": {
    "index_type": "VRA_ZONES",
    "n_zones": 3,
    "scene_id": "optional-scene-uuid"
  }
}
```

**Notes:**

- If `scene_id` is omitted, the backend uses the latest NDVI raster from the index cache.
- The job runs asynchronously (Celery task). Poll `GET /api/vegetation/jobs/{job_id}` until `status == "completed"`.
- Hectare limits apply (controlled per tenant by `LimitsValidator`). Returns `429` if exceeded.

---

## GeoJSON Feature Format

Every zoning feature returned by the API follows this contract:

```json
{
  "type": "Feature",
  "properties": {
    "zone_id": 1,
    "cluster_id": 1,
    "zone_class": "medium",
    "mean_value": 0.4521,
    "area_ha": 4.12,
    "prescription_rate": 1.08
  },
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[lon, lat], [lon, lat], ..., [lon, lat]]]
  }
}
```

**Field specifications:**

| Field | Type | Description |
|-------|------|-------------|
| `properties.zone_id` | integer | Zone index (0 to n_zones-1). Primary identifier for GIS/machinery integration. |
| `properties.cluster_id` | integer | K-means cluster label. Equivalent to `zone_id`. |
| `properties.zone_class` | string | Vigor class: `"low"`, `"medium"`, `"high"`. Based on centroid-ordered NDVI values. |
| `properties.mean_value` | number | Mean NDVI for this zone, rounded to 4 decimals. |
| `properties.area_ha` | number | Zone area in hectares (approximate, from pixel count). |
| `properties.prescription_rate` | number | Input rate multiplier (0.5 to 2.0). Low vigor → reduce, high vigor → increase. |
| `geometry.type` | string | Always `"Polygon"` in current implementation. |
| `geometry.coordinates` | array | WGS 84 `[longitude, latitude]` pairs. Outer ring is counterclockwise. |

**Vigor to prescription mapping:**
- `"low"` → `prescription_rate < 1.0` (reduce inputs)
- `"medium"` → `prescription_rate ≈ 1.0` (baseline)
- `"high"` → `prescription_rate > 1.0` (increase inputs)

**Coordinate precision:** 6-7 decimal places (~10 cm at the equator).

**Consuming notes:**
- `zone_id` starts at 0 for the lowest-vigor zone and increments to n_zones-1 for the highest.
- Adjacent zones with the same cluster_id are merged during vectorization. A feature may cover disconnected areas.

---

## Authentication

All endpoints use the same authentication chain as the rest of the Nekazari platform:

1. **Bearer token (primary):** Send the Keycloak JWT as `Authorization: Bearer <token>`. The token is verified against the platform's JWKS endpoint (`{JWT_ISSUER}/protocol/openid-connect/certs`) with strict issuer validation.
2. **httpOnly cookie (fallback):** If no `Authorization` header is present, the backend reads the `nkz_token` cookie. This is the mechanism used by the Nekazari web frontend.
3. **Tenant isolation:** The `X-Tenant-ID` header is required on every request. Data is filtered by `tenant_id` at the database level. A tenant can only access its own parcels and jobs.

**Required JWT claims:**

| Claim | Expected |
|-------|----------|
| `iss` | Must match `JWT_ISSUER` env var (exact match, no suffix tolerance) |
| `sub` | User ID |
| `email` | User email |
| `preferred_username` | Username |
| `realm_access.roles` | Roles for RBAC |

**Authorization roles:** The module requires at least one of: `Farmer`, `TenantAdmin`, `PlatformAdmin`.

---

## Orion-LD Integration

When zoning completes, the backend upserts each zone as an independent NGSI-LD entity:

**Entity type:** `AgriManagementZone`

**Entity ID pattern:**
```
urn:ngsi-ld:AgriManagementZone:{parcel_suffix}:Z{cluster_id}
```

Example for parcel `urn:ngsi-ld:AgriParcel:abc123`, zone 0:
```
urn:ngsi-ld:AgriManagementZone:abc123:Z0
```

**Entity structure:**

```json
{
  "id": "urn:ngsi-ld:AgriManagementZone:abc123:Z0",
  "type": "AgriManagementZone",
  "refAgriParcel": {
    "type": "Relationship",
    "object": "urn:ngsi-ld:AgriParcel:abc123"
  },
  "location": {
    "type": "GeoProperty",
    "value": {
      "type": "Polygon",
      "coordinates": [[[lon, lat], ...]]
    }
  },
  "zoneName": {
    "type": "Property",
    "value": "Zone 1"
  },
  "zoneId": {
    "type": "Property",
    "value": 0
  },
  "zoneClass": {
    "type": "Property",
    "value": "low"
  },
  "prescriptionRate": {
    "type": "Property",
    "value": 0.96
  },
  "areaHa": {
    "type": "Property",
    "value": 2.85
  },
  "variableAttribute": {
    "type": "Property",
    "value": "NDVI"
  }
}
```

**Consuming from Orion-LD:** Other modules can query management zones by subscribing to `AgriManagementZone` entities or querying the Context Broker directly:

```
GET /ngsi-ld/v1/entities?type=AgriManagementZone&q=refAgriParcel==%22urn:ngsi-ld:AgriParcel:abc123%22
```

**Important:** The Orion-LD GeoJSON uses `location.value.geometry` -- the VRA API wraps it in a top-level `FeatureCollection` for direct consumption.

---

## Permissions (manifest.json)

The module's `manifest.json` declares these API access paths for VRA zoning:

```
/api/vegetation/jobs                       (create, list)
/api/vegetation/jobs/zoning/{parcel_id}/geojson   (read zoning)
/api/vegetation/export/{parcel_id}/geojson        (export geojson)
/api/vegetation/export/{parcel_id}/shapefile       (export shapefile)
/api/vegetation/export/{parcel_id}/csv             (export csv)
```

These paths must be allowed in the consuming module's API access configuration if accessed cross-module via the api-gateway.

---

## Error Handling

All errors follow the FastAPI/JSON convention:

```json
{
  "detail": "Human-readable error message"
}
```

| HTTP Code | Typical Cause |
|-----------|---------------|
| `401` | Missing or expired JWT, invalid issuer |
| `400` | Missing `X-Tenant-ID` header |
| `404` | No zoning data for parcel, or parcel not found |
| `422` | Invalid job UUID format |
| `429` | Hectare processing limit exceeded (job creation) |
| `500` | Internal processing error (DB, raster, or storage) |
| `501` | Shapefile export: `fiona`/`shapely` not installed |

---

## Example: Consuming from GIS Module

A GIS module (e.g., QGIS plugin or web map) should use the zoning GeoJSON endpoint as the primary data source.

**Request (Python):**

```python
import requests

headers = {
    "Authorization": "Bearer <jwt>",
    "X-Tenant-ID": "my-tenant",
}

resp = requests.get(
    "https://nkz.robotika.cloud/api/vegetation/jobs/zoning/urn:ngsi-ld:AgriParcel:abc123/geojson",
    headers=headers,
)
resp.raise_for_status()
zones = resp.json()

# zones is a GeoJSON FeatureCollection -- load directly into map library
# Example with folium:
import folium
m = folium.Map(location=[42.0, 2.5], zoom_start=15)
for feature in zones["features"]:
    cluster = feature["properties"]["cluster_id"]
    color = ["#ff0000", "#ffff00", "#00ff00"][cluster]
    folium.GeoJson(
        feature,
        style_function=lambda x, c=color: {"fillColor": c, "color": c, "weight": 2},
    ).add_to(m)
m.save("zones.html")
```

**For offline/desktop GIS:** Use the export endpoint to download Shapefile:

```python
resp = requests.get(
    "https://nkz.robotika.cloud/api/vegetation/export/urn:ngsi-ld:AgriParcel:abc123/shapefile",
    headers=headers,
)
with open("prescription_abc123.zip", "wb") as f:
    f.write(resp.content)
# Unzip and open the .shp in QGIS/ArcGIS
```

**For layer styling in a web map (e.g., Mapbox, Leaflet, Cesium):**

```javascript
// Color mapping: assign colors to cluster_ids
const COLOR_MAP = { 0: "#d73027", 1: "#fee08b", 2: "#1a9850" };

fetch(url, { headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant } })
  .then(r => r.json())
  .then(geojson => {
    map.addSource("vra-zones", { type: "geojson", data: geojson });
    map.addLayer({
      id: "vra-zones-fill",
      type: "fill",
      source: "vra-zones",
      paint: {
        "fill-color": ["get", "cluster_id"],
        "fill-color-type": "categorical",
        "fill-color-values": COLOR_MAP,
        "fill-opacity": 0.6,
        "fill-outline-color": "#000000",
      },
    });
  });
```

---

## Example: Consuming from Machinery Guidance

Farm machinery guidance systems (ISO 11783 / ISOBUS compatible tractors, sprayers, spreaders) require prescription maps as ISOXML or Shapefile.

### Option A: ISOXML via export_service (on the backend)

The `export_service.py` module supports ISO 11783-10 TaskData XML generation. To use it, call the export service programmatically from within the platform:

```python
from app.services.export_service import exporter

# Assuming features are already retrieved from a completed job
features = [...]  # list of GeoJSON Feature dicts

isoxml_bytes = exporter.export_isoxml(
    features=features,
    task_name="VRA_N_2026-04-29",
    product_name="Nitrogen_27%",
    default_rate=100.0,
    rate_property="application_rate",  # uses cluster_id as proxy; map cluster_id to rate
)

# Write to file or return as HTTP response
with open("/output/TASKDATA.zip", "wb") as f:
    f.write(isoxml_bytes)
```

**ISOXML output structure (zipped):**

```
TASKDATA/
  TASKDATA.XML    # ISO 11783-10 TaskData XML
  GRD00001.BIN    # Binary grid of treatment zone IDs
```

The XML contains a `<TSK>` (Task) with `<TZN>` (Treatment Zone) elements and per-zone `<PDV>` (Process Data Variable) entries carrying application rates.

### Option B: Shapefile import into farm software

Download the zipped Shapefile from the export endpoint and import into:

- **John Deere Operations Center:** Upload `.zip` with Shapefile components
- **Trimble / Ag Leader / Raven:** Most support Shapefile import for prescription maps; the EPSG:4326 CRS is standard
- **CNH / Case IH AFS:** Import via AFS Connect or USB with Shapefile

### Option C: Manual rate mapping

The raw `cluster_id` (0, 1, 2) in the GeoJSON is an abstract zone label, not an application rate. Machinery guidance systems expect a rate value (kg/ha or L/ha). Map cluster IDs to real rates using the following logic:

| Cluster ID | Vigor Level | Typical N Rate | Typical Seed Rate |
|------------|-------------|----------------|-------------------|
| 0 (low)    | Low vigor   | 120 kg/ha      | 60,000 seeds/ha   |
| 1 (medium) | Medium vigor| 100 kg/ha      | 70,000 seeds/ha   |
| 2 (high)   | High vigor  | 80 kg/ha       | 80,000 seeds/ha   |

**Note:** Cluster ID to vigor mapping is NOT guaranteed to be ordered by centroid value. When running in machinery guidance integration, sort zones by their mean NDVI before assigning rates. To obtain mean NDVI per zone, either:

1. Read `statistics` from the job result (`GET /api/vegetation/jobs/{job_id}`) -- detailed zonal statistics may be present on the completed job.
2. Compute zonal statistics by overlaying the GeoJSON on the source NDVI raster.
3. Query Orion-LD for the `AgriManagementZone` entities and derive from zone centroids.

### Option D: CSV for spreadsheet-to-machinery pipeline

Download CSV for intermediate processing in a spreadsheet or custom script before converting to the target machinery format:

```bash
curl -H "Authorization: Bearer $JWT" -H "X-Tenant-ID: $TENANT" \
  -o prescription_abc123.csv \
  "https://nkz.robotika.cloud/api/vegetation/export/urn:ngsi-ld:AgriParcel:abc123/csv"
```

---

## Rate Limits & Usage Tracking

- Job creation is subject to per-tenant hectare limits (`LimitsValidator`), checked against `admin_platform.tenant_limits` in PostgreSQL.
- The `GET` zoning and export endpoints are not rate-limited (read-only).
- Usage is recorded via `UsageTracker.record_job_usage()` on job creation.

---

## N8N / Intelligence Module Integration

The `ZoningAlgorithm.generate_zones()` result payload includes `webhook_metadata` for N8N workflow compatibility:

```json
{
  "webhook_metadata": {
    "intelligence_module_compatible": true,
    "n8n_ready": true,
    "can_delegate_to": ["intelligence-module", "n8n-workflow"]
  }
}
```

The `prepare_for_intelligence_module()` method prepares pixel data for external ML clustering services (DBSCAN, spectral clustering, deep clustering) with a `callback_endpoint` for result delivery at `/api/vegetation/jobs/zoning/callback`.

---

## Implementation Reference

Source files for the VRA zoning implementation:

| File | Role |
|------|------|
| `backend/app/jobs/zoning_algorithm.py` | K-means clustering, vectorization, Orion-LD entity creation |
| `backend/app/api/jobs.py` | REST endpoints for job CRUD and GeoJSON retrieval |
| `backend/app/api/export.py` | Export endpoints (GeoJSON, Shapefile, CSV) |
| `backend/app/services/export_service.py` | Export format serialization (includes ISOXML for ISOBUS) |
| `backend/app/tasks.py` | Celery task dispatching for async zoning computation |
| `backend/app/middleware/auth.py` | JWT verification and tenant extraction |
| `manifest.json` | Module metadata and API access permissions |
