"""
Internal endpoints for DataHub federation — Apache Arrow IPC timeseries export.

Called exclusively by the DataHub BFF scatter-gather engine, never directly by users.
Auth: The BFF forwards the original user JWT + X-Tenant-ID header, so standard
require_auth applies.

Contract (ADAPTER_SPEC.md):
  POST /api/internal/timeseries/export-arrow
  Body:  { series: [{entity_id, attribute}], start_time, end_time, resolution }
  Response: Arrow IPC stream, Content-Type: application/vnd.apache.arrow.stream
  Timestamp column: Float64 Unix SECONDS (not milliseconds)
  Value columns:
    - Single series  → "value"
    - Multiple series → "value_0", "value_1", ...
"""

import hmac
import io
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pyarrow as pa
import pyarrow.ipc
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.parcels import observations_response, parcel_urn_of
from app.database import SessionLocal, get_db_with_tenant
from app.middleware.auth import require_auth, get_tenant_id
from app.models import VegetationIndexCache, VegetationScene, VegetationSubscription
from app.services.monitoring import Period, monitoring_period
from app.services.monitoring_candidates import candidates, invalidate as invalidate_candidates, parcel_state
from app.services.monitoring_io import add_continuous, continuous_modules, fetch_timeline, remove_continuous
from app.services.observation_series import BrokerUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])

ARROW_MIME = "application/vnd.apache.arrow.stream"

# Maps DataHub attribute names → vegetation_indices_cache.index_type
# Extend this dict when new indices are added.
ATTRIBUTE_TO_INDEX: dict[str, str] = {
    "ndviMean": "NDVI",
    "eviMean":  "EVI",
    "saviMean": "SAVI",
    "osaviMean": "OSAVI",
    "gndviMean": "GNDVI",
    "ndreMean": "NDRE",
    "ndmiMean": "NDMI",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SeriesRequest(BaseModel):
    entity_id: str
    attribute: str


class ArrowExportRequest(BaseModel):
    series: List[SeriesRequest]
    start_time: str   # ISO-8601, e.g. "2026-01-01T00:00:00Z"
    end_time: str     # ISO-8601
    resolution: int = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_to_epoch_seconds(d: date) -> float:
    """Convert a date to Float64 Unix epoch seconds (midnight UTC).

    NOTE: The DataHub contract (ADAPTER_SPEC.md) requires seconds, not
    milliseconds. uPlot and the BFF Polars code both expect seconds.
    """
    return float(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _build_arrow_response(series_data: list[tuple[list, list]]) -> bytes:
    """
    Build an Arrow IPC stream from a list of (timestamps, values) tuples.

    Single series  → columns: timestamp, value
    Multiple series → columns: timestamp, value_0, value_1, ...

    All types are Float64 as required by the DataHub contract.
    """
    if not series_data:
        schema = pa.schema([
            pa.field("timestamp", pa.float64()),
            pa.field("value", pa.float64()),
        ])
        table = pa.table({"timestamp": pa.array([], type=pa.float64()),
                          "value":     pa.array([], type=pa.float64())},
                         schema=schema)
    elif len(series_data) == 1:
        ts, vals = series_data[0]
        table = pa.table({
            "timestamp": pa.array(ts,   type=pa.float64()),
            "value":     pa.array(vals, type=pa.float64()),
        })
    else:
        # Align multiple series on a shared timestamp axis (union, NaN for gaps).
        all_ts = sorted({t for ts, _ in series_data for t in ts})
        columns: dict[str, pa.Array] = {
            "timestamp": pa.array(all_ts, type=pa.float64()),
        }
        for i, (ts, vals) in enumerate(series_data):
            ts_to_val = dict(zip(ts, vals))
            aligned = [ts_to_val.get(t, float("nan")) for t in all_ts]
            columns[f"value_{i}"] = pa.array(aligned, type=pa.float64())
        table = pa.table(columns)

    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def _downsample(ts: list, vals: list, resolution: int) -> tuple[list, list]:
    """Thin a series to at most `resolution` points by uniform stride."""
    if resolution <= 0 or len(ts) <= resolution:
        return ts, vals
    step = max(1, len(ts) // resolution)
    return ts[::step], vals[::step]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/api/internal/timeseries/export-arrow")
async def export_arrow(
    body: ArrowExportRequest,
    _auth: dict = Depends(require_auth),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db_with_tenant),
):
    """Export timeseries as Arrow IPC for DataHub scatter-gather.

    Queries vegetation_indices_cache (zonal statistics — mean_value) joined
    with vegetation_scenes for the sensing date.  Raw pixel arrays are never
    transmitted.
    """
    if not body.series:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="series array must not be empty",
        )

    try:
        start_dt = datetime.fromisoformat(body.start_time.replace("Z", "+00:00")).date()
        end_dt   = datetime.fromisoformat(body.end_time.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ISO-8601 time: {exc}",
        )

    series_data: list[tuple[list, list]] = []

    for serie in body.series:
        index_type = ATTRIBUTE_TO_INDEX.get(serie.attribute)
        if not index_type:
            logger.warning(
                "Unknown attribute '%s' for entity '%s' — returning empty series.",
                serie.attribute, serie.entity_id,
            )
            series_data.append(([], []))
            continue

        rows = (
            db.query(VegetationScene.sensing_date, VegetationIndexCache.mean_value)
            .join(VegetationIndexCache, VegetationIndexCache.scene_id == VegetationScene.id)
            .filter(
                VegetationIndexCache.tenant_id == tenant_id,
                VegetationIndexCache.entity_id == serie.entity_id,
                VegetationIndexCache.index_type == index_type,
                VegetationScene.sensing_date >= start_dt,
                VegetationScene.sensing_date <= end_dt,
            )
            .order_by(VegetationScene.sensing_date.asc())
            .all()
        )

        ts   = [_date_to_epoch_seconds(r.sensing_date) for r in rows]
        vals = [float(r.mean_value) if r.mean_value is not None else float("nan")
                for r in rows]

        ts, vals = _downsample(ts, vals, body.resolution)
        series_data.append((ts, vals))

    arrow_bytes = _build_arrow_response(series_data)
    return Response(content=arrow_bytes, media_type=ARROW_MIME)


# ---------------------------------------------------------------------------
# Parcel lifecycle: setup/teardown
# ---------------------------------------------------------------------------

INTERNAL_SECRET = os.getenv("INTERNAL_SERVICE_SECRET", "")


class SetupParcelRequest(BaseModel):
    parcel_id: str
    tenant_id: str
    parcel_name: str = ""
    action: str = "activate"  # "activate" | "deactivate" | "teardown"


async def _fetch_parcel_location(
    parcel_id: str, tenant_id: str
) -> tuple[dict | None, float | None, float | None]:
    """Fetch AgriParcel geometry from Orion-LD, return (geojson, lat, lon)."""
    try:
        from nkz_platform_sdk import SyncOrionClient
        orion = SyncOrionClient(tenant_id)
        from app.services.fiware_integration import orion_get_entity
        entity = orion_get_entity(orion, parcel_id)
        if entity is None:
            logger.warning("Parcel %s not found in Orion", parcel_id)
            return None, None, None
        location = entity.get("location")
        if isinstance(location, dict):
            location = location.get("value") or location
        if not isinstance(location, dict) or "coordinates" not in location:
            return None, None, None
        from shapely.geometry import shape
        geom = shape(location)
        centroid = geom.centroid
        return location, centroid.y, centroid.x
    except Exception as exc:
        logger.warning("Failed to resolve parcel location: %s", exc)
        return None, None, None


# The beat's `process_subscriptions` is the only thing that downloads Sentinel-2
# and searches Sentinel-1 for a parcel, and it works off `vegetation_subscriptions`.
# Activating the module on a parcel must therefore own that row.
SUBSCRIPTION_BACKFILL_DAYS = int(os.getenv("SUBSCRIPTION_BACKFILL_DAYS", "60"))
SUBSCRIPTION_INDEX_TYPES = ("NDVI", "EVI", "SAVI", "NDRE", "GNDVI", "NDMI")


def _subscription_wkt(geometry: dict) -> str:
    from shapely.geometry import MultiPolygon, shape

    geom = shape(geometry)
    if geom.geom_type == "Polygon":
        geom = MultiPolygon([geom])
    return f"SRID=4326;{geom.wkt}"


def _find_subscription(db: Session, tenant_id: str, parcel_urn: str) -> VegetationSubscription | None:
    return (
        db.query(VegetationSubscription)
        .filter(VegetationSubscription.tenant_id == tenant_id, VegetationSubscription.entity_id == parcel_urn)
        .first()
    )


def _ensure_subscription(db: Session, tenant_id: str, parcel_urn: str, geometry: dict,
                         period: Period | None = None) -> str:
    """Create the parcel's monitoring subscription, or reactivate a paused one.

    ``period`` is the crop's monitoring period; without it the default window applies.
    Reactivating takes the period start, clears the pause reason and restarts the cursor.
    An active subscription that starts after the period is widened to it (cursor restarted);
    otherwise it is left untouched, so a repeated call changes nothing.
    Returns ``created`` | ``reactivated`` | ``widened`` | ``unchanged``.
    """
    sub = _find_subscription(db, tenant_id, parcel_urn)
    if sub is None:
        start = period.start if period else date.today() - timedelta(days=SUBSCRIPTION_BACKFILL_DAYS)
        db.add(VegetationSubscription(
            tenant_id=tenant_id,
            entity_id=parcel_urn,
            entity_type="AgriParcel",
            geometry=_subscription_wkt(geometry),
            start_date=start,
            index_types=list(SUBSCRIPTION_INDEX_TYPES),
            frequency="weekly",
            is_active=True,
            status="created",
        ))
        db.commit()
        return "created"
    if sub.is_active:
        if period is not None and sub.start_date > period.start:
            sub.start_date = period.start
            sub.last_run_at = None
            sub.next_run_at = None
            db.commit()
            return "widened"
        return "unchanged"
    sub.is_active = True
    sub.paused_reason = None
    sub.next_run_at = None  # due on the next beat
    if period is not None:
        sub.start_date = period.start
        sub.last_run_at = None
    db.commit()
    return "reactivated"


def _deactivate_subscription(db: Session, tenant_id: str, parcel_urn: str) -> bool:
    """Pause the parcel's subscription as a person's pause; its scenes and indices stay.

    A pause after the harvest becomes the person's too, so it never resumes by itself.
    True when something changed.
    """
    sub = _find_subscription(db, tenant_id, parcel_urn)
    if sub is None or (not sub.is_active and sub.paused_reason == "user"):
        return False
    sub.is_active = False
    sub.paused_reason = "user"
    db.commit()
    return True


def _enqueue_lst(tenant_id: str, parcel_id: str, geometry: dict, lat: float, lon: float) -> None:
    """Queue the Landsat and CLMS land surface temperature tasks for one parcel."""
    from app.tasks.lst_tasks import process_parcel_lst_task, process_parcel_clms_lst_task
    for task in (process_parcel_lst_task, process_parcel_clms_lst_task):
        task.delay(
            tenant_id=tenant_id,
            parcel_id=parcel_id,
            latitude=lat,
            longitude=lon,
            geometry_geojson=geometry,
        )


@router.post("/api/internal/setup-parcel", status_code=201)
async def setup_parcel(request: Request, body: SetupParcelRequest):
    """Internal endpoint called by entity-manager on parcel activation.

    Validates X-Internal-Service-Secret (defense-in-depth, primary auth is
    api-gateway OIDC). Activation creates (or reactivates) the parcel's
    monitoring subscription, which is what the nightly beat processes for
    Sentinel-2 and Sentinel-1, and queues the first land surface temperature.
    Deactivation and teardown pause the subscription and keep the data as is.
    """
    secret = request.headers.get("X-Internal-Service-Secret", "")
    if not INTERNAL_SECRET or not hmac.compare_digest(secret, INTERNAL_SECRET):
        logger.warning("Unauthorized internal setup-parcel call from %s", request.client)
        raise HTTPException(status_code=401, detail="Unauthorized")
    if body.action not in ("activate", "deactivate", "teardown"):
        raise HTTPException(status_code=400, detail="Invalid action")

    parcel_urn = body.parcel_id
    if not parcel_urn.startswith("urn:ngsi-ld:AgriParcel:"):
        parcel_urn = f"urn:ngsi-ld:AgriParcel:{parcel_urn}"
    invalidate_candidates(body.tenant_id)  # whatever happens next, the cached list is stale

    if body.action in ("deactivate", "teardown"):
        paused = False
        db = None
        try:
            db = SessionLocal()
            paused = _deactivate_subscription(db, body.tenant_id, parcel_urn)
        except Exception as exc:
            logger.warning("Subscription pause failed for %s: %s", parcel_urn, exc)
        finally:
            if db is not None:
                db.close()
        message = "deactivated" if body.action == "deactivate" else "torn_down"
        logger.info("%s vegetation-health for parcel %s (subscription paused=%s)", message, parcel_urn, paused)
        return {"message": message, "parcel_id": body.parcel_id, "subscription_paused": paused}

    geometry, lat, lon = await _fetch_parcel_location(parcel_urn, body.tenant_id)
    lst_enqueued = False
    subscription = "skipped"
    if geometry and lat is not None and lon is not None:
        timeline = await fetch_timeline(body.tenant_id, parcel_urn)
        if timeline is None:
            logger.warning("crop timeline unavailable for %s: default monitoring window", parcel_urn)
        db = None
        try:
            db = SessionLocal()
            period = None
            if timeline is not None:
                continuous = continuous_modules(db, body.tenant_id, parcel_urn)
                period = monitoring_period(timeline, date.today(), bool(continuous))
            subscription = _ensure_subscription(db, body.tenant_id, parcel_urn, geometry, period)
        except Exception as exc:
            subscription = "error"
            logger.error("Subscription setup failed for %s: %s", parcel_urn, exc)
        finally:
            if db is not None:
                db.close()
        try:
            _enqueue_lst(body.tenant_id, parcel_urn, geometry, lat, lon)
            lst_enqueued = True
        except Exception as exc:
            logger.warning("LST enqueue failed for %s: %s", parcel_urn, exc)
    else:
        logger.warning("No parcel geometry for %s: subscription and LST not set up", parcel_urn)
    logger.info("Activated vegetation-health for parcel %s (subscription=%s)", parcel_urn, subscription)
    return {"message": "activated", "parcel_id": body.parcel_id, "lst_enqueued": lst_enqueued,
            "subscription": subscription}


def _require_internal_secret(request: Request, what: str) -> None:
    """401 unless the request carries the shared internal secret."""
    secret = request.headers.get("X-Internal-Service-Secret", "")
    # Bytes, so a non-ASCII header is a 401 and not a TypeError (500).
    if not INTERNAL_SECRET or not hmac.compare_digest(secret.encode("utf-8"), INTERNAL_SECRET.encode("utf-8")):
        logger.warning("Unauthorized internal %s call from %s", what, request.client)
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_tenant(tenant_id: Optional[str]) -> str:
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    return tenant_id


@router.get("/api/internal/vegetation/parcels/{entity_id}/observations")
async def internal_parcel_observations(
    request: Request,
    entity_id: str,
    tenant_id: Optional[str] = Query(None),
    index: str = Query("NDVI"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
):
    """Observation series for backends of other modules (read only).

    Authenticated by X-Internal-Service-Secret, like setup-parcel; the tenant is
    explicit because no user identity travels with the call.
    """
    _require_internal_secret(request, "observations")
    return await observations_response(_require_tenant(tenant_id), entity_id, index, start, end)


@router.get("/api/internal/vegetation/parcels/{entity_id}/monitoring")
async def internal_parcel_monitoring(request: Request, entity_id: str, tenant_id: Optional[str] = Query(None)):
    """Crop, monitoring status and period of a parcel, for backends of other modules."""
    _require_internal_secret(request, "monitoring")
    tenant_id = _require_tenant(tenant_id)
    db = SessionLocal()
    try:
        return await parcel_state(db, tenant_id, parcel_urn_of(entity_id), date.today())
    except BrokerUnavailable:
        raise HTTPException(status_code=502, detail="Context broker unavailable; the crop could not be read")
    finally:
        db.close()


@router.get("/api/internal/vegetation/monitoring/candidates")
async def internal_monitoring_candidates(request: Request, tenant_id: Optional[str] = Query(None)):
    """Parcels of the tenant with a living crop and no monitoring."""
    _require_internal_secret(request, "monitoring candidates")
    tenant_id = _require_tenant(tenant_id)
    db = SessionLocal()
    try:
        return {"tenant_id": tenant_id, "candidates": await candidates(db, tenant_id, date.today())}
    except BrokerUnavailable:
        raise HTTPException(status_code=502, detail="Context broker unavailable; the crops could not be read")
    finally:
        db.close()


class ContinuousRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)


@router.post("/api/internal/vegetation/parcels/{entity_id}/continuous", status_code=204)
async def request_continuous_monitoring(request: Request, entity_id: str, body: ContinuousRequest):
    """A module asks that the parcel is never paused after its harvest. Idempotent."""
    _require_internal_secret(request, "continuous request")
    db = SessionLocal()
    try:
        add_continuous(db, body.tenant_id, parcel_urn_of(entity_id), body.module_id)
    finally:
        db.close()
    invalidate_candidates(body.tenant_id)
    return Response(status_code=204)


@router.delete("/api/internal/vegetation/parcels/{entity_id}/continuous", status_code=204)
async def withdraw_continuous_monitoring(request: Request, entity_id: str, body: ContinuousRequest):
    """A module withdraws its request. Idempotent."""
    _require_internal_secret(request, "continuous withdrawal")
    db = SessionLocal()
    try:
        remove_continuous(db, body.tenant_id, parcel_urn_of(entity_id), body.module_id)
    finally:
        db.close()
    invalidate_candidates(body.tenant_id)
    return Response(status_code=204)
