"""Parcel size check endpoint for vegetation-health module."""
import logging
import os
import re
from fastapi import APIRouter, Depends, HTTPException, Request

from app.middleware.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vegetation/parcels", tags=["parcels"])

MAX_PARCEL_HA = float(os.getenv("VEGETATION_MAX_PARCEL_HA", "500"))
ADMIN_OVERRIDE_HEADER = "X-Override-Max-HA"


def _wkt_area_ha(wkt_str: str) -> float:
    """Compute area in hectares from a WKT string using shapely."""
    try:
        from shapely import wkt
        from pyproj import Transformer
        geom = wkt.loads(wkt_str)
        if geom.is_empty:
            return 0.0
        centroid = geom.centroid
        utm_zone = int((centroid.x + 180) / 6) + 1
        epsg = 32600 + utm_zone if centroid.y >= 0 else 32700 + utm_zone
        transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        geom_utm = transformer.transform(geom)
        return geom_utm.area / 10000
    except ImportError:
        logger.warning("shapely/pyproj not available, using rough estimate")
        return 0.0
    except Exception as e:
        logger.warning("Failed to compute parcel area: %s", e)
        return 0.0


@router.get("/{parcel_id}/check-size")
async def check_parcel_size(
    parcel_id: str,
    request: Request,
    current_user: dict = Depends(require_auth),
):
    """Check if parcel area exceeds the limit for vegetation index processing."""
    import requests as req
    from app.services.fiware_integration import ORION_URL, _make_headers

    tenant_id = current_user["tenant_id"]
    parcel_urn = f"urn:ngsi-ld:AgriParcel:{parcel_id}"
    headers = _make_headers(tenant_id)

    try:
        resp = req.get(
            f"{ORION_URL}/ngsi-ld/v1/entities/{parcel_urn}",
            headers=headers,
            timeout=10,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to query Orion-LD: {e}")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Parcel not found")
    if not resp.ok:
        raise HTTPException(status_code=502, detail="Orion-LD error")

    entity = resp.json()
    location = entity.get("location", {}).get("value", {})
    wkt_str = entity.get("parcelGeometryWKT", {}).get("value", "")

    if not wkt_str and location:
        from shapely.geometry import shape
        try:
            geom = shape(location)
            wkt_str = geom.wkt
        except Exception:
            pass

    area_ha = _wkt_area_ha(wkt_str) if wkt_str else 0.0

    limit = MAX_PARCEL_HA
    override = request.headers.get(ADMIN_OVERRIDE_HEADER)
    if override:
        try:
            limit = float(override)
        except ValueError:
            pass

    return {
        "area_ha": round(area_ha, 2),
        "exceeds_limit": area_ha > limit,
        "limit_ha": limit,
    }
