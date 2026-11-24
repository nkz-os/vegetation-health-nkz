"""Per-management-zone statistics and EOProduct ids.

Targets are the zones of the current multi-date zoning of the parcel
(``AgriManagementZone``, relation ``hasAgriManagementZone``). Products already
stored for weather-map terrain units (``hasAgriParcelZone``) stay as history. Zone products never
carry ``hasAgriParcel``, so every existing parcel-level query keeps returning the
same entities.
"""

import logging
from typing import Any, Dict, List, Optional

import numpy as np
from nkz_platform_sdk import SyncOrionClient
from nkz_platform_sdk.gis.zones import ZONE_TYPE, default_zoning, group_zonings

logger = logging.getLogger(__name__)

MIN_ZONE_PIXELS = 20
MIN_VALID_FRACTION = 0.5

_ZONE_TYPE_KEYS = ("zoneType", "nkz:zoneType", "https://nkz-os.org/ns/zoneType")
_ZONE_ID_KEYS = ("nkz:zoneId", "zoneId", "https://nkz-os.org/ns/zoneId")
_ZONE_VERSION_KEYS = ("nkz:zoneVersion", "zoneVersion", "https://nkz-os.org/ns/zoneVersion")
_ZONE_LEVEL_KEYS = ("zoneLevel", "nkz:zoneLevel", "https://nkz-os.org/ns/zoneLevel")
_MULTIDATE_METHOD = "vegetationIndexMultiDate"
CURRENT_ZONE_RELATION = "hasAgriManagementZone"


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
    """urn:ngsi-ld:EOProduct:{tenant}:zone:{parcel_short}:{zone_id}[:GRD]:{YYYY-MM-DD}

    The ``zone`` segment right after the tenant keeps zone products out of every
    parcel-prefix query (``…:{tenant}:{parcel_short}:%``).
    """
    date_str = sensing_date.strftime("%Y-%m-%d") if hasattr(sensing_date, "strftime") else str(sensing_date)[:10]
    mid = "GRD:" if grd else ""
    return f"urn:ngsi-ld:EOProduct:{tenant}:zone:{parcel_short}:{zone_id}:{mid}{date_str}"


def fetch_current_zones(tenant_id: str, parcel_urn: str) -> List[Dict[str, Any]]:
    """Zones of the parcel's current zoning, only when it is a multi-date vegetation run.

    Each item is ``{zone_id, zone_urn, zone_version, geometry, relation, level, run_id}``
    with ``zone_id = {run_id[:8]}-{level}``. Any other current zoning (older single
    image method, manual, none), or a run whose levels are missing or repeated,
    gives ``[]``. Fail-safe: any Orion error returns ``[]``.
    """
    try:
        entities = SyncOrionClient(tenant_id).query_entities(
            type=ZONE_TYPE, q=f'hasAgriParcel=="{parcel_urn}"', limit=1000,
        )
    except Exception as exc:
        logger.warning("Current zoning lookup failed for %s: %s", parcel_urn, exc)
        return []
    entities = entities if isinstance(entities, list) else []
    try:
        current = default_zoning(group_zonings(entities))
    except Exception as exc:
        logger.warning("Current zoning grouping failed for %s: %s", parcel_urn, exc)
        return []
    if current is None:
        logger.info("No current zones for %s (reason=no_zoning)", parcel_urn)
        return []
    if current.method != _MULTIDATE_METHOD:
        logger.info("No current zones for %s (reason=method=%s)", parcel_urn, current.method)
        return []
    by_id = {e.get("id"): e for e in entities}
    zones: List[Dict[str, Any]] = []
    seen_levels = set()
    for ref in current.zones:
        level = _first(by_id.get(ref.id) or {}, _ZONE_LEVEL_KEYS)
        if not isinstance(level, str) or not level or level in seen_levels:
            logger.warning("Current zoning %s of %s has a missing or repeated level, ignored",
                           current.run_id, parcel_urn)
            return []
        if not isinstance(ref.geometry, dict) or "coordinates" not in ref.geometry:
            logger.warning("Zone %s has no usable geometry, skipped", ref.id)
            continue
        seen_levels.add(level)
        zones.append({
            "zone_id": f"{current.run_id[:8]}-{level}",
            "zone_urn": ref.id,
            "zone_version": current.run_id,
            "geometry": ref.geometry,
            "relation": CURRENT_ZONE_RELATION,
            "level": level,
            "run_id": current.run_id,
        })
    return zones


def fetch_zone_targets(tenant_id: str, parcel_urn: str) -> List[Dict[str, Any]]:
    """Zones that get products: only the parcel's current zoning (terrain units no longer)."""
    return fetch_current_zones(tenant_id, parcel_urn)


def zone_mask(geometry: dict, transform, shape, crs=None) -> np.ndarray:
    """Exact (unbuffered) mask of a 4326 GeoJSON zone on a raster grid. True = excluded.

    Same convention as ``create_geometry_mask`` but without its outward buffer,
    so adjacent or nested zones do not share pixels. ``crs`` is the raster CRS the
    geometry is reprojected to (omit for a geometry already in it).
    """
    from rasterio.features import geometry_mask
    from shapely.geometry import shape as shp

    geom = shp(geometry)
    if crs and str(crs) != "EPSG:4326":
        from pyproj import Transformer
        from shapely.ops import transform as shp_transform

        geom = shp_transform(Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform, geom)
    return geometry_mask([geom], out_shape=tuple(shape), transform=transform,
                         all_touched=False, invert=False)


def zone_statistics(processor, index_array: np.ndarray, zone_geometry: dict,
                    exact: bool = False) -> Optional[Dict[str, Any]]:
    """Statistics of ``index_array`` inside a zone, or None when the zone is too thin.

    None when fewer than MIN_ZONE_PIXELS valid pixels or valid pixels are under
    MIN_VALID_FRACTION of the zone's pixels (``valid_fraction``).
    """
    if exact:
        # Current-zoning zones are nested/adjacent: the 10 m buffer of
        # create_geometry_mask would pull in pixels of the neighbouring level.
        meta = processor.band_meta
        mask = zone_mask(zone_geometry, meta["transform"], (meta["height"], meta["width"]),
                         crs=meta.get("crs"))
    else:
        # NOTE: create_geometry_mask buffers by PARCEL_GEOMETRY_BUFFER_M (10 m), so
        # terrain-unit counts are slightly inflated.
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
                         index_array: np.ndarray, sensing_date, cloud_cover=None, *,
                         legacy: bool = True, satimg=None) -> int:
    """Publish one optical zone product per zone target; per-zone failures are logged.

    ``legacy`` writes the EOProduct; ``satimg`` (an ``eo_satimg_optical.ZoneWrite``, set
    only when the parcel's EOSatelliteImagery took the day) writes the zone's new form.
    Never raises: zone work must not change the parcel result. Returns the number
    of zone products published (legacy form).
    """
    from app.services.fiware_integration import upsert_eo_zone_index

    parcel_short = parcel_urn.split(":")[-1][:10]
    published = 0
    try:
        zones = fetch_zone_targets(tenant_id, parcel_urn)
    except Exception as exc:
        logger.warning("Zone discovery failed for %s: %s", parcel_urn, exc)
        return 0
    for zone in zones:
        try:
            stats = zone_statistics(processor, index_array, zone["geometry"],
                                    exact=zone.get("relation") == CURRENT_ZONE_RELATION)
            if stats is None:
                logger.info("Zone %s skipped (too few valid pixels)", zone["zone_id"])
                continue
            if legacy and upsert_eo_zone_index(tenant_id, parcel_short, zone, sensing_date,
                                               index_type.lower(), stats, cloud_cover=cloud_cover):
                published += 1
            if satimg is not None and zone.get("relation") == CURRENT_ZONE_RELATION:
                _publish_zone_satimg(tenant_id, parcel_urn, zone, index_type, stats, cloud_cover, satimg)
        except Exception as exc:
            logger.warning("Zone %s product failed for %s: %s", zone["zone_id"], parcel_urn, exc)
    return published


def _publish_zone_satimg(tenant_id, parcel_urn, zone, index_type, stats, cloud_cover, satimg) -> None:
    from app.services import eo_satimg_optical as so

    analytics = so.index_analytics(index_type, stats)
    analytics["pixelCount"] = stats.get("pixel_count")
    analytics["validPixelFraction"] = stats.get("valid_fraction")
    if satimg.take.family.value == "s2-indices":
        analytics["cloudCoverage"] = so.cloud_coverage(cloud_cover)
    so.write_optical(tenant_id, so.zone_target(parcel_urn, zone["zone_urn"], zone.get("geometry")),
                     so.zone_take(satimg.take), analytics, index_type=index_type, follow=satimg.parcel_verdict)


def delete_zone_products(tenant_id: str, parcel_urn: str, sensing_date, index_type: str,
                         job_result: Optional[Dict[str, Any]] = None, keep_if=None) -> None:
    """Remove ``index_type`` from the parcel's zone products of one date, both forms. Fail-safe, never raises.

    ``job_result`` dates the EOSatelliteImagery take (a composite by its window end);
    without it the single-scene take of ``sensing_date`` is assumed. The new form is left alone when
    the job wrote nothing (skipped) or ``keep_if()`` says another live job owns the value.
    """
    from app.services import eo_satimg_optical as so
    from app.services.fiware_integration import delete_eo_index

    try:
        parcel_short = parcel_urn.split(":")[-1][:10]
        family_day = so.job_family_day(job_result or {"sensing_date": str(sensing_date)})
        for zone in fetch_zone_targets(tenant_id, parcel_urn):
            try:
                delete_eo_index(tenant_id, zone_eo_product_id(tenant_id, parcel_short, zone["zone_id"], sensing_date),
                                index_type)
            except Exception as exc:
                logger.warning("Zone product cleanup failed for %s: %s", zone["zone_id"], exc)
            if family_day and zone.get("relation") == CURRENT_ZONE_RELATION and not so.job_wrote_nothing(job_result):
                so.remove_index(tenant_id, "zone", zone["zone_urn"], family_day[0], family_day[1], index_type,
                                job_result, keep_if)
    except Exception as exc:
        logger.warning("Zone product cleanup failed for %s: %s", parcel_urn, exc)
