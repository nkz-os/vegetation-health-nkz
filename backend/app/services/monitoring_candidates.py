"""Parcel monitoring state and the parcels that need activation, read for a tenant.

The candidates answer reads every crop of the tenant, so it is cached per tenant in
the memory of the process for a few minutes and dropped whenever monitoring changes
here (activation, deactivation, continuity requests). Another process keeps its own
copy until it expires.
"""
import asyncio
import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional

from nkz_platform_sdk import OrionClient
from sqlalchemy.orm import Session

from app.models import VegetationContinuousRequest, VegetationSubscription
from app.services.fiware_integration import _attr_by_name, _property_value
from app.services.monitoring import monitoring_state
from app.services.monitoring_io import continuous_modules, fetch_timeline
from app.services.observation_series import BrokerUnavailable

logger = logging.getLogger(__name__)

PAGE_SIZE = 500
MAX_PAGES = 100
MAX_CONCURRENT_READS = 5
CACHE_TTL_SECONDS = 300

_CROP_RELATIONSHIPS = ("hasAgriCrop", "refAgriCrop")
_cache: Dict[str, tuple[float, List[dict]]] = {}


def invalidate(tenant_id: str) -> None:
    """Forget the tenant's cached candidates."""
    _cache.pop(tenant_id, None)


def _has_crop(parcel: dict) -> bool:
    for name in _CROP_RELATIONSHIPS:
        rel = _attr_by_name(parcel, name)
        if isinstance(rel, dict):
            rel = rel.get("object") or rel.get("value")
        if rel:
            return True
    return False


def _name_of(parcel: dict) -> Optional[str]:
    value = _property_value(_attr_by_name(parcel, "name"))
    return value if isinstance(value, str) else None


async def parcels_with_crop(tenant_id: str) -> List[Dict[str, Any]]:
    """``[{id, name}]`` of the tenant's parcels that have a crop assigned.

    All parcels are listed (paged) and filtered here: a ``q`` on the relationship
    depends on how the broker expands its name.
    """
    client = OrionClient(tenant_id)
    found: List[Dict[str, Any]] = []
    try:
        offset = 0
        for _ in range(MAX_PAGES):
            page = await client.query_entities(type="AgriParcel", limit=PAGE_SIZE, offset=offset,
                                               options="keyValues") or []
            found.extend({"id": p.get("id"), "name": _name_of(p)} for p in page if _has_crop(p))
            if len(page) < PAGE_SIZE:
                return found
            offset += PAGE_SIZE
        raise BrokerUnavailable("parcel list truncated")
    except BrokerUnavailable:
        raise
    except Exception as exc:
        logger.warning("parcel listing failed tenant=%s: %s", tenant_id, exc)
        raise BrokerUnavailable(str(exc)) from exc
    finally:
        await client.close()


async def parcel_state(db: Session, tenant_id: str, parcel_urn: str, today: date) -> dict:
    """The monitoring state of one parcel; BrokerUnavailable when its crop cannot be read."""
    timeline = await fetch_timeline(tenant_id, parcel_urn)
    if timeline is None:
        raise BrokerUnavailable(f"crop timeline unavailable for {parcel_urn}")
    subscription = (
        db.query(VegetationSubscription)
        .filter(VegetationSubscription.tenant_id == tenant_id, VegetationSubscription.entity_id == parcel_urn)
        .first()
    )
    return monitoring_state(timeline, subscription, continuous_modules(db, tenant_id, parcel_urn), today)


async def candidates(db: Session, tenant_id: str, today: date) -> List[dict]:
    """Parcels with a living crop and no monitoring, with the period they would be watched over.

    Any failed crop read fails the whole answer: a partial list would hide parcels.
    """
    cached = _cache.get(tenant_id)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    parcels = await parcels_with_crop(tenant_id)
    gate = asyncio.Semaphore(MAX_CONCURRENT_READS)

    async def read(parcel_id: str) -> Optional[dict]:
        async with gate:
            return await fetch_timeline(tenant_id, parcel_id)

    timelines = await asyncio.gather(*(read(p["id"]) for p in parcels))
    if any(t is None for t in timelines):
        raise BrokerUnavailable("crop timeline unavailable for some parcels")
    subscriptions = {
        s.entity_id: s for s in db.query(VegetationSubscription).filter(VegetationSubscription.tenant_id == tenant_id).all()
    }
    requests: Dict[str, List[str]] = {}
    for row in db.query(VegetationContinuousRequest).filter(VegetationContinuousRequest.tenant_id == tenant_id).all():
        requests.setdefault(row.entity_id, []).append(row.module_id)
    result = []
    for parcel, timeline in zip(parcels, timelines):
        state = monitoring_state(timeline, subscriptions.get(parcel["id"]), requests.get(parcel["id"], []), today)
        if state["needs_activation"]:
            result.append({
                "entity_id": parcel["id"],
                "name": parcel["name"],
                "species": state["crop"]["species"],
                "lifecycle": state["crop"]["lifecycle"],
                "period": state["period"],
            })
    _cache[tenant_id] = (time.monotonic(), result)
    return result
