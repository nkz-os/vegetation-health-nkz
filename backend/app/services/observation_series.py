"""Per-parcel observation series read from the broker's current EOProduct state.

One EOProduct per (parcel, acquisition date) holds each index as a Property with the
interior measure as sub-properties. The series reads that state, not the telemetry
history (where every republication leaves another row), and marks each date with a
quality instead of dropping it.
"""

import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from nkz_platform_sdk import SyncOrionClient

from app.services.fiware_integration import _attr_by_name, _property_value

logger = logging.getLogger(__name__)

MIN_VALID_FRACTION = 0.7
PAGE_SIZE = 1000
SUPPORTED_INDICES = ("NDVI", "EVI", "SAVI", "OSAVI", "GNDVI", "NDRE", "NDMI")

_PARCEL_RELATIONSHIPS = ("hasAgriParcel", "refAgriParcel")
_ID_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})$")


class BrokerUnavailable(Exception):
    """The broker could not be read: the caller must not present it as an empty series."""


def _sensing_date(entity: dict) -> Optional[str]:
    value = _property_value(_attr_by_name(entity, "sensingDate"))
    if isinstance(value, str) and _ID_DATE.fullmatch(value[:10]):
        return value[:10]
    match = _ID_DATE.search(str(entity.get("id", "")))
    return match.group(1) if match else None


def _number(prop) -> Optional[float]:
    value = _property_value(prop)
    return float(value) if value is not None else None


def quality_of(point: dict) -> str:
    if point.get("valid_fraction") is None:
        return "unmeasured"
    if point.get("mask") == "parcel":
        return "too_few_pixels"
    if point["valid_fraction"] < MIN_VALID_FRACTION:
        return "partly_covered"
    return "ok"


def point_from_product(entity: dict, index: str) -> Optional[dict]:
    """One point from an EOProduct, or None when it carries no such index or no date."""
    attr = _attr_by_name(entity, index.lower())
    sensing = _sensing_date(entity)
    if not isinstance(attr, dict) or sensing is None:
        return None
    count = _property_value(_attr_by_name(attr, "interiorPixelCount"))
    point = {
        "date": sensing,
        "median": _number(_attr_by_name(attr, "interiorMedian")),
        "mean": _number(_attr_by_name(attr, "interiorMean")),
        "p10": _number(_attr_by_name(attr, "interiorP10")),
        "p90": _number(_attr_by_name(attr, "interiorP90")),
        "valid_fraction": _number(_attr_by_name(attr, "interiorValidFraction")),
        "pixel_count": int(count) if count is not None else None,
        "cloud_fraction": _number(_attr_by_name(entity, "parcelCloudFraction")),
        "mask": _property_value(_attr_by_name(attr, "interiorMask")),
    }
    point["quality"] = quality_of(point)
    return point


def _points_to(entity: dict, parcel_urn: str) -> bool:
    for name in _PARCEL_RELATIONSHIPS:
        rel = _attr_by_name(entity, name)
        if isinstance(rel, dict) and (rel.get("object") or rel.get("value")) == parcel_urn:
            return True
    return False


def build_series(entities: List[dict], parcel_urn: str, index: str,
                 start: Optional[date], end: Optional[date]) -> List[dict]:
    """Ascending points for the parcel: one per date (best valid fraction), dates filtered."""
    best: Dict[str, dict] = {}
    for entity in entities:
        if not _points_to(entity, parcel_urn):
            continue
        point = point_from_product(entity, index)
        if point is None:
            continue
        day = date.fromisoformat(point["date"])
        if (start and day < start) or (end and day > end):
            continue
        current = best.get(point["date"])
        score = point["valid_fraction"] if point["valid_fraction"] is not None else -1
        if current is None or score > (current["valid_fraction"] if current["valid_fraction"] is not None else -1):
            best[point["date"]] = point
    return [best[d] for d in sorted(best)]


def fetch_parcel_products(tenant_id: str, parcel_urn: str) -> List[dict]:
    """The tenant's EOProducts whose id belongs to the parcel's 10-character prefix.

    The SDK client has no ``idPattern``: every EOProduct of the tenant is paged
    (``limit=1000``) and filtered by id here. The prefix can collide between parcels;
    ``build_series`` filters by relationship.
    """
    short = parcel_urn.split(":")[-1][:10]
    pattern = re.compile(rf"^urn:ngsi-ld:EOProduct:{re.escape(tenant_id)}:{re.escape(short)}:\d{{4}}-\d{{2}}-\d{{2}}$")
    found: List[dict] = []
    try:
        client = SyncOrionClient(tenant_id)
        offset = 0
        while True:
            page = client.query_entities(type="EOProduct", limit=PAGE_SIZE, offset=offset)
            found.extend(e for e in page if pattern.match(str(e.get("id", ""))))
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    except Exception as exc:
        logger.warning("broker read failed for parcel %s tenant %s: %s", parcel_urn, tenant_id, exc)
        raise BrokerUnavailable(str(exc)) from exc
    return found


def observation_series(tenant_id: str, parcel_urn: str, index: str,
                       start: Optional[date], end: Optional[date], *, woody: bool) -> Dict[str, Any]:
    index = index.upper()
    points = build_series(fetch_parcel_products(tenant_id, parcel_urn), parcel_urn, index, start, end)
    response: Dict[str, Any] = {
        "entity_id": parcel_urn,
        "index": index,
        "rules": {"min_valid_fraction": MIN_VALID_FRACTION, "mask": "interior"},
        "points": points,
    }
    if woody:
        response["canopy_basis"] = "parcel_interior"
    return response
