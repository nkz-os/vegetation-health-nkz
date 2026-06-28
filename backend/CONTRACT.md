# Vegetation-Health `EOProduct` Read Contract

Audience: external consumers of canonical vegetation-index data — ML module,
BioOrchestrator, Crop-Health. Documents what the code in this repo actually
writes and serves; not an aspirational spec.

Source: `backend/app/services/fiware_integration.py` (`upsert_eo_index`,
`_entity_id_for_acquisition`, `_index_attribute`) and
`backend/app/api/timeseries_adapter.py`.

## 1. Entity: `EOProduct`

SDM family: `dataModel.SatelliteImagery` (see §6).

One `EOProduct` entity per **(parcel, acquisition/sensingDate)** — all
vegetation indices computed for that acquisition are merged as named
Properties onto the same entity (one entity, not one entity per index).

**Entity id scheme** (`_entity_id_for_acquisition`):

```
urn:ngsi-ld:EOProduct:{tenant}:{parcelShort10}:{sensingDate}
```

- `{tenant}` — canonical tenant id (hyphenated, e.g. `montiko`).
- `{parcelShort10}` — the parcel URN's **last `:`-separated segment**,
  truncated to its **first 10 characters**. Documented as-is: this is a
  truncation of the parcel's local id, not a hash; two parcels whose local
  id agrees in the first 10 characters will collide. There is no other
  uniqueness in the id for this segment.
- `{sensingDate}` — `YYYY-MM-DD` (`sensing_date.isoformat()`).

  ASSUMPTION: there is a second, older id helper (`_entity_id_for_eo_product`,
  used by `upsert_eo_product` for SAR/GRD backscatter products) that inserts
  a `:GRD:` segment before the date. That path is for SAR backscatter, not
  for the optical vegetation indices this contract covers, and is out of
  scope here.

**Always present on every `EOProduct`:**

| Attribute | NGSI-LD type | Notes |
|---|---|---|
| `hasAgriParcel` | `Relationship` | object = parcel URN. SDM-standard relationship name (not `refAgriParcel`). |
| `sensingDate` | `Property` (string) | `YYYY-MM-DD`. |
| `pixelCount` | `Property` (int) | valid pixel count for the zonal stats. |
| `source` | `Property` (string) | constant `"vegetation_health"`. |
| `cloudCoverPercentage` | `Property` (number) | only present when cloud cover was supplied; rounded to 2 decimals. |

One or more **index attributes** (§2) are merged in alongside the above —
which ones are present on a given entity depends on which indices were
computed for that acquisition.

**Upsert semantics:** `POST /ngsi-ld/v1/entities` first; on `409` (entity
already exists for this parcel+date), `PATCH .../attrs` merges the new
index attribute (and the always-present attributes) into the existing
entity. Successive index computations for the same acquisition accumulate
onto one entity rather than creating duplicates.

## 2. Index attributes

Attribute key = the index name, **lowercased**: `ndvi`, `ndre`, `savi`,
`gndvi`, `evi`, `lst`. These are exactly the index types the module computes and
persists (CHECK constraint in `app/models/indices.py` and
`app/models/config.py`: `NDVI`, `EVI`, `SAVI`, `GNDVI`, `NDRE`, plus an
internal `CUSTOM`). `LST` (Land Surface Temperature from Landsat C2L2-ST) is
written via `upsert_eo_lst` — not through the optical index pipeline.
heuristics (`app/services/anomaly_detection.py`) for classifying *other*
modules' index names — it is never written by `upsert_eo_index` and will
not appear as an `EOProduct` attribute from this module.

Shape of each index attribute (`_index_attribute`):

```jsonc
"ndvi": {
  "type": "Property",
  "value": 0.642,          // zonal MEAN over the parcel, dimensionless, ∈ [-1, 1]
  "observedAt": "2026-06-20T10:50:00Z",
  "min":   { "type": "Property", "value": 0.301 },
  "max":   { "type": "Property", "value": 0.811 },
  "std":   { "type": "Property", "value": 0.084 },
  "rasterUrl":   { "type": "Property", "value": "https://.../ndvi_20260620.tif" },
  "previewUrl":  { "type": "Property", "value": "https://.../ndvi_20260620.png" }
}
```

- `value` — zonal **mean**, rounded to 6 decimals. Dimensionless, expected
  range `[-1, 1]` for all five indices.
- `min` / `max` / `std` — zonal min/max/standard deviation as sub-Property
  `Property`s (not sub-attributes of `value` — siblings of it inside the
  same attribute object), rounded to 6 decimals.
- `observedAt` — fixed at `10:50:00Z` on the sensing date for every index on
  every acquisition (not the true satellite overpass time).
- `rasterUrl` — pointer (string `Property`) to the full-resolution COG in
  object storage. **Omitted entirely** if no raster was produced for that
  index.
- `previewUrl` — pointer (string `Property`) to a PNG preview. Same
  omit-if-absent rule.

`rasterUrl`/`previewUrl` are pointers only — the broker never holds raster
bytes (see §3c).

### 2.1 LST attribute (`lst`)

Land Surface Temperature from Landsat 8/9 Collection 2 Level-2 ST (`ST_B10`),
zonal mean over the parcel geometry. Written by `upsert_eo_lst` (not the optical
index calculator). Unit: degrees Celsius (`unitCode`: `CEL`).

```jsonc
"lst": {
  "type": "Property",
  "value": 32.4,
  "unitCode": "CEL",
  "observedAt": "2026-06-20T10:50:00Z",
  "min": { "type": "Property", "value": 28.1 },
  "max": { "type": "Property", "value": 36.7 },
  "std": { "type": "Property", "value": 2.3 }
}
```

Optional top-level `lstSourceScene` (string Property) holds the CDSE STAC scene id.

## 3. Read paths

There are three independent read paths. Pick the one matching your need;
do not mix them.

### (a) Historical timeseries — Arrow IPC

```
GET /api/timeseries/entities/{entity_id}/data
    ?attribute={index}&start_time={iso8601}&end_time={iso8601}[&resolution={n}]&format=arrow
```

- `{entity_id}` is the **exact `EOProduct` entity URN** (the id scheme in
  §1), **not** the parcel URN — the query is `entity_id = %s`, an exact
  match against `telemetry_events.entity_id`. Since one `EOProduct` exists
  per acquisition, a single call only returns the rows for that one
  acquisition's row(s) in `telemetry_events` (effectively 0 or 1 point per
  attribute per entity, sliced by the time window). To pull a parcel's full
  history across acquisitions, callers need the BFF endpoint (§3a-bis) or
  must iterate per-acquisition entity ids.
- `attribute` — required; lowercase index name (`ndvi`, `evi`, `savi`,
  `gndvi`, `ndre`); matched as `payload[attribute.lower()]`.
- `start_time` / `end_time` — ISO 8601; `start_time` inclusive, `end_time`
  exclusive (`observed_at >= start_time AND observed_at < end_time`). Naive
  datetimes are treated as UTC.
- `resolution` — optional; if the row count exceeds it, the series is
  decimated by simple striding (not averaging).
- `format` — only `arrow` is accepted; anything else is `400`.
- Auth: standard `require_auth` (gateway-injected `X-Tenant-ID` etc.,
  see §4). Tenant comes from the authenticated user, not from the URL.
- **Response (data present):** Arrow IPC stream
  (`application/vnd.apache.arrow.stream`), two columns:
  - `timestamp` — `float64`, Unix epoch **seconds**.
  - `value` — `float64`, the index's zonal mean (`payload[attribute]["value"]`).
- **Response (no data):** `204 No Content` (no body).

### (a-bis) Historical timeseries — BFF JSON (frontend chart)

```
GET /api/vegetation/bff/history
    ?entity_id={parcelURN}&index_type={NDVI|EVI|SAVI|GNDVI|NDRE}&start={iso8601}&end={iso8601}
```

- `entity_id` here **is the parcel URN** (despite the query-param name) —
  the handler derives the `EOProduct` id prefix
  (`urn:ngsi-ld:EOProduct:{tenant}:{parcelShort10}:`) and does a `LIKE`
  match against `telemetry_events.entity_id`, returning every acquisition
  for that parcel in the window. This is the endpoint that gives a true
  multi-point parcel history; the raw Arrow adapter in (a) does not.
- `index_type` — case-insensitive index name (any casing; lowercased
  internally), default `NDVI`.
- Response: `{"points": [{"date": ..., "mean": ..., "min": ..., "max": ...,
  "std": ...}, ...]}`, ascending by `observed_at`. Empty window →
  `{"points": []}` (HTTP 200, not 204).
- Both (a) and (a-bis) read from the `telemetry_events` hypertable, which is
  populated by the platform's NGSI-LD subscription → telemetry-worker
  pipeline (TimescaleDB is never written to directly by this module — see
  platform directive on zero direct DB writes for time-series data). Orion-LD
  remains the source of truth; `telemetry_events` is a derived read replica.

### (b) Current value — Orion-LD query

```
GET {orion_base}/ngsi-ld/v1/entities?type=EOProduct&q=hasAgriParcel=={parcelURN}
```

Required headers:

- `NGSILD-Tenant: {tenant}` and `Fiware-Service: {tenant}` (canonical
  hyphenated tenant id, e.g. `montiko`).
- Platform `@context` Link header (NOT `application/ld+json` for a plain
  GET without a body):
  ```
  Link: <https://nekazari.robotika.cloud/ngsi-ld-context.json>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"
  ```
  Omitting this Link header makes Orion expand `EOProduct`/`hasAgriParcel`
  against the default NGSI-LD vocabulary instead of the platform's SDM
  context, which silently returns zero results (the same false-zero trap
  documented for `AgriParcel`). Prefer `SyncOrionClient`/`OrionClient` from
  `nkz-platform-sdk`, or `inject_fiware_headers()`, over hand-rolled
  headers — both inject this Link automatically.
- Returns every `EOProduct` for the parcel (one per acquisition); sort/filter
  by `sensingDate` client-side to get the latest. There is no
  `?orderBy=`/`?limit=1` shortcut documented here — verify against your
  Orion-LD version if you need server-side ordering.

  ASSUMPTION: the module's own `CONTEXT_URL` env var defaults to the
  in-cluster `http://api-gateway-service:5000/ngsi-ld-context.json` for its
  own writes (internal, pod-to-pod). External callers resolving the context
  over a Link header from outside the cluster (or from another module's
  pod, depending on DNS) should use the public
  `https://nekazari.robotika.cloud/ngsi-ld-context.json` form shown above —
  the two must resolve to the same context document.

### (c) Rasters — object storage, never the broker

`rasterUrl` (COG, GeoTIFF) and `previewUrl` (PNG) on each index attribute
are direct links into the module's MinIO bucket (S3 API). Fetch them
directly with an HTTP client; **never** attempt to read raster bytes from
Orion-LD — the broker only ever holds the pointer strings. If an index has
no raster for a given acquisition, the corresponding attribute key
(`rasterUrl`/`previewUrl`) is absent from that index Property entirely
(not present-with-null).

## 4. Tenant scoping

Every read path is tenant-scoped:

- HTTP API (§3a, §3a-bis): gateway-injected `X-Tenant-ID` header, resolved
  to `current_user["tenant_id"]` server-side via `require_auth`. Callers
  authenticate through the api-gateway; they do not set tenant headers
  directly on this module.
- Orion-LD (§3b): `NGSILD-Tenant` + `Fiware-Service`, both set to the
  canonical hyphenated tenant id (e.g. `montiko`, not `Montiko` or
  `montiko_test`).
- There is one `EOProduct` namespace per tenant — the tenant id is embedded
  in the entity id itself (§1), so cross-tenant leakage at the id level is
  not possible by construction; it still requires sending the correct
  tenant headers to query it back out.

## 5. Example `EOProduct` entity

A parcel with two indices (`ndvi`, `gndvi`) computed for the same
acquisition, both merged onto one entity, plus cloud cover:

```json
{
  "@context": ["https://nekazari.robotika.cloud/ngsi-ld-context.json"],
  "id": "urn:ngsi-ld:EOProduct:montiko:a1b2c3d4e5:2026-06-20",
  "type": "EOProduct",
  "hasAgriParcel": {
    "type": "Relationship",
    "object": "urn:ngsi-ld:AgriParcel:montiko:a1b2c3d4e5f6"
  },
  "sensingDate": { "type": "Property", "value": "2026-06-20" },
  "pixelCount": { "type": "Property", "value": 18342 },
  "source": { "type": "Property", "value": "vegetation_health" },
  "cloudCoverPercentage": { "type": "Property", "value": 4.2 },
  "ndvi": {
    "type": "Property",
    "value": 0.642,
    "observedAt": "2026-06-20T10:50:00Z",
    "min": { "type": "Property", "value": 0.301 },
    "max": { "type": "Property", "value": 0.811 },
    "std": { "type": "Property", "value": 0.084 },
    "rasterUrl": {
      "type": "Property",
      "value": "https://minio.robotika.cloud/vegetation-health/montiko/a1b2c3d4e5f6/ndvi/2026-06-20.tif"
    },
    "previewUrl": {
      "type": "Property",
      "value": "https://minio.robotika.cloud/vegetation-health/montiko/a1b2c3d4e5f6/ndvi/2026-06-20.png"
    }
  },
  "gndvi": {
    "type": "Property",
    "value": 0.558,
    "observedAt": "2026-06-20T10:50:00Z",
    "min": { "type": "Property", "value": 0.252 },
    "max": { "type": "Property", "value": 0.733 },
    "std": { "type": "Property", "value": 0.079 },
    "rasterUrl": {
      "type": "Property",
      "value": "https://minio.robotika.cloud/vegetation-health/montiko/a1b2c3d4e5f6/gndvi/2026-06-20.tif"
    },
    "previewUrl": {
      "type": "Property",
      "value": "https://minio.robotika.cloud/vegetation-health/montiko/a1b2c3d4e5f6/gndvi/2026-06-20.png"
    }
  }
}
```

(`urn:ngsi-ld:AgriParcel:montiko:a1b2c3d4e5f6` is the kind of id whose last
segment, truncated to 10 chars, produces `a1b2c3d4e5` above — illustrating
§1's `{parcelShort10}` rule, including its collision caveat.)

## 6. SDM reference

`EOProduct` follows the FIWARE Smart Data Model
[`dataModel.SatelliteImagery`](https://github.com/smart-data-models/dataModel.SatelliteImagery)
family (`EOProduct` type). This module does not implement the full SDM
attribute set — only the attributes documented in §1–§2 above are written;
treat any SDM attribute not listed here as absent.
