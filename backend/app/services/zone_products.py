"""Per-management-zone statistics and EOProduct ids.

Zones are published by the weather-map module as ``AgriParcelZone`` entities.
Zone products carry ``hasAgriParcelZone`` and never ``hasAgriParcel``, so every
existing parcel-level query keeps returning the same entities.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import numpy as np
from nkz_platform_sdk import OrionClient

logger = logging.getLogger(__name__)

MIN_ZONE_PIXELS = 20
MIN_VALID_FRACTION = 0.5

_ZONE_TYPE_KEYS = ("zoneType", "nkz:zoneType", "https://nkz-os.org/ns/zoneType")
_ZONE_ID_KEYS = ("nkz:zoneId", "zoneId", "https://nkz-os.org/ns/zoneId")
_ZONE_VERSION_KEYS = ("nkz:zoneVersion", "zoneVersion", "https://nkz-os.org/ns/zoneVersion")


def _unwrap(attr: Any) -> Any:
    """Value of an NGSI-LD attribute in normalized or keyValues form."""
    if isinstance(attr, dict):
        for k in ("value", "object"):
            if k in attr:
                return attr[k]
        return None
    return attr


def _first(entity: Dict[str, Any], keys) -> Any:
    for k in keys:
        if k in entity:
            v = _unwrap(entity[k])
            if v is not None:
                return v
    return None


def zone_eo_product_id(tenant: str, parcel_short: str, zone_id: str, sensing_date, grd: bool = False) -> str:
    """urn:ngsi-ld:EOProduct:{tenant}:{parcel_short}:{zone_id}[:GRD]:{YYYY-MM-DD}"""
    date_str = sensing_date.strftime("%Y-%m-%d") if hasattr(sensing_date, "strftime") else str(sensing_date)[:10]
    mid = "GRD:" if grd else ""
    return f"urn:ngsi-ld:EOProduct:{tenant}:{parcel_short}:{zone_id}:{mid}{date_str}"


def fetch_management_zones(tenant_id: str, parcel_urn: str) -> List[Dict[str, Any]]:
    """Management zones of a parcel as ``{zone_id, zone_urn, zone_version, geometry}``.

    Fail-safe: any Orion error returns ``[]`` (zone products are additive).
    """
    async def _go() -> List[Dict[str, Any]]:
        orion = OrionClient(tenant_id)
        try:
            return await orion.query_entities(
                type="AgriParcelZone",
                q=f'hasAgriParcel=="{parcel_urn}"|refAgriParcel=="{parcel_urn}"',
                limit=1000,
            )
        finally:
            await orion.close()

    try:
        entities = asyncio.run(_go())
    except Exception as exc:
        logger.warning("Zone lookup failed for %s: %s", parcel_urn, exc)
        return []

    zones: List[Dict[str, Any]] = []
    for e in entities if isinstance(entities, list) else []:
        if _first(e, _ZONE_TYPE_KEYS) != "management":
            continue
        rel = _unwrap(e.get("hasAgriParcel")) or _unwrap(e.get("refAgriParcel"))
        if rel != parcel_urn:
            continue
        geometry = _unwrap(e.get("location"))
        if not isinstance(geometry, dict) or "coordinates" not in geometry:
            logger.warning("Zone %s has no usable geometry, skipped", e.get("id"))
            continue
        zone_id = _first(e, _ZONE_ID_KEYS) or str(e.get("id", "")).split(":")[-1]
        zones.append({
            "zone_id": str(zone_id),
            "zone_urn": e["id"],
            "zone_version": _first(e, _ZONE_VERSION_KEYS),
            "geometry": geometry,
        })
    return zones


def zone_statistics(processor, index_array: np.ndarray, zone_geometry: dict) -> Optional[Dict[str, Any]]:
    """Statistics of ``index_array`` inside a zone, or None when the zone is too thin.

    None when fewer than MIN_ZONE_PIXELS valid pixels or valid pixels are under
    MIN_VALID_FRACTION of the zone's pixels (``valid_fraction``).
    """
    mask = processor.create_geometry_mask(zone_geometry)  # True = excluded
    if mask.shape != index_array.shape:
        rows = (np.arange(index_array.shape[0]) * mask.shape[0]) // index_array.shape[0]
        cols = (np.arange(index_array.shape[1]) * mask.shape[1]) // index_array.shape[1]
        mask = mask[np.ix_(rows, cols)]
    mask_pixels = int(np.count_nonzero(~mask))
    if mask_pixels == 0:
        return None
    stats = processor.calculate_statistics(index_array, mask=mask)
    valid_fraction = stats["pixel_count"] / mask_pixels
    if stats["pixel_count"] < MIN_ZONE_PIXELS or valid_fraction < MIN_VALID_FRACTION:
        return None
    return {**stats, "valid_fraction": valid_fraction}


def publish_zone_indices(tenant_id: str, parcel_urn: str, index_type: str, processor,
                         index_array: np.ndarray, sensing_date, cloud_cover=None) -> int:
    """Publish one optical zone product per management zone; per-zone failures are logged.

    Never raises: zone work must not change the parcel result. Returns the number
    of zone products published.
    """
    from app.services.fiware_integration import upsert_eo_zone_index

    parcel_short = parcel_urn.split(":")[-1][:10]
    published = 0
    try:
        zones = fetch_management_zones(tenant_id, parcel_urn)
    except Exception as exc:
        logger.warning("Zone discovery failed for %s: %s", parcel_urn, exc)
        return 0
    for zone in zones:
        try:
            stats = zone_statistics(processor, index_array, zone["geometry"])
            if stats is None:
                logger.info("Zone %s skipped (too few valid pixels)", zone["zone_id"])
                continue
            if upsert_eo_zone_index(tenant_id, parcel_short, zone, sensing_date,
                                    index_type.lower(), stats, cloud_cover=cloud_cover):
                published += 1
        except Exception as exc:
            logger.warning("Zone %s product failed for %s: %s", zone["zone_id"], parcel_urn, exc)
    return published
