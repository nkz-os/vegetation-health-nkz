"""Internal endpoints for parcel activation workflow."""
import logging
import os
import hmac
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/internal", tags=["internal"])

_INTERNAL_SECRET: str | None = None


def _get_internal_secret() -> str:
    global _INTERNAL_SECRET
    if _INTERNAL_SECRET is None:
        _INTERNAL_SECRET = os.getenv("INTERNAL_SERVICE_SECRET", "")
    return _INTERNAL_SECRET


def _verify_internal_secret(request: Request) -> None:
    """Verify X-Internal-Service-Secret header."""
    secret = _get_internal_secret()
    provided = request.headers.get("X-Internal-Service-Secret", "")
    if not secret or not hmac.compare_digest(secret, provided):
        raise HTTPException(status_code=403, detail="Invalid internal service secret")


async def _fetch_parcel_location(
    parcel_id: str, tenant_id: str
) -> tuple[dict | None, float | None, float | None]:
    """Fetch AgriParcel geometry from Orion-LD, return (geojson, lat, lon)."""
    try:
        from nkz_platform_sdk import SyncOrionClient
        orion = SyncOrionClient(tenant_id)
        resp = orion.get(f"/ngsi-ld/v1/entities/{parcel_id}")
        if resp.status_code != 200:
            logger.warning("Parcel %s not found in Orion (%d)", parcel_id, resp.status_code)
            return None, None, None
        entity = resp.json()
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


@router.post("/setup-parcel")
async def setup_parcel(
    request: Request,
    db: Session = Depends(get_db_session),
):
    """Called by entity-manager when activating this module for a parcel.
    
    Body: { parcel_id, tenant_id }
    This endpoint is authenticated ONLY by X-Internal-Service-Secret.
    Dispatches a Landsat LST Celery task for this parcel.
    """
    _verify_internal_secret(request)
    body = await request.json()
    parcel_id = body.get("parcel_id")
    tenant_id = body.get("tenant_id")
    if not parcel_id or not tenant_id:
        raise HTTPException(status_code=400, detail="parcel_id and tenant_id required")
    
    logger.info("Setting up vegetation-prime for parcel %s / tenant %s", parcel_id, tenant_id)

    try:
        geometry_geojson, lat, lon = await _fetch_parcel_location(parcel_id, tenant_id)
        if geometry_geojson and lat is not None and lon is not None:
            from app.tasks.lst_tasks import process_parcel_lst_task
            process_parcel_lst_task.delay(
                tenant_id=tenant_id,
                parcel_id=parcel_id,
                latitude=lat,
                longitude=lon,
                geometry_geojson=geometry_geojson,
            )
            logger.info("Enqueued LST task for parcel %s", parcel_id)
        else:
            logger.warning("No parcel geometry for %s — LST task skipped", parcel_id)
    except Exception as exc:
        logger.warning("LST task enqueue failed for %s: %s", parcel_id, exc)

    return {"status": "ok", "parcel_id": parcel_id, "tenant_id": tenant_id}
