"""Reads that feed the monitoring rules: the parcel's crop timeline and the modules asking for continuity."""
import logging
from typing import List, Optional

import httpx
from nkz_platform_sdk import OrionClient
from nkz_platform_sdk.crop_timeline import fetch_crop_timeline
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import VegetationContinuousRequest
from app.services.fiware_integration import _attr_by_name, _property_value
from app.services.monitoring import needs_parcel_crop

logger = logging.getLogger(__name__)


def _text(value) -> Optional[str]:
    """Plain string of a keyValues or normalized attribute, None when absent."""
    if isinstance(value, dict):
        value = value.get("object") or value.get("@value") or _property_value(value)
    if isinstance(value, list):
        value = value[0] if value else None
    return value if isinstance(value, str) and value else None


def _is_missing(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response is not None and exc.response.status_code == 404


async def fetch_parcel_crop(tenant_id: str, parcel_urn: str) -> Optional[dict]:
    """The parcel's own crop (AgriParcel -> AgriCrop), as ``{species, lifecycle, status, end}``.

    None when the parcel or its crop does not exist or none is assigned. Any other
    failure raises: it is not "no crop".
    """
    client = OrionClient(tenant_id)
    try:
        try:
            parcel = await client.get_entity(parcel_urn, options="keyValues")
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise
        crop_id = _text(_attr_by_name(parcel, "hasAgriCrop")) or _text(_attr_by_name(parcel, "refAgriCrop"))
        if not crop_id:
            return None
        try:
            crop = await client.get_entity(crop_id, options="keyValues")
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise
        end = _text(_attr_by_name(crop, "actualTerminationDate")) or _text(_attr_by_name(crop, "terminationDate"))
        return {
            "species": _text(_attr_by_name(crop, "species")),
            "lifecycle": _text(_attr_by_name(crop, "cropLifecycle")),
            "status": _text(_attr_by_name(crop, "status")),
            "end": {"date": end, "provenance": None},
        }
    finally:
        await client.close()


async def fetch_timeline(tenant_id: str, parcel_urn: str) -> Optional[dict]:
    """The parcel's crop timeline, or None when the broker could not be read.

    None is a failed read, never "no crop": a parcel without a crop has a timeline
    with no current or previous cycle. The caller must then change nothing.

    The SDK timeline only knows crops with a registered sowing. When it has none, the
    parcel's own crop is read and attached as ``parcel_crop`` (only a woody crop uses
    it, see ``monitoring``); a failure of that read is a failed read too.
    """
    try:
        timeline = await fetch_crop_timeline(tenant_id, parcel_urn)
        if timeline is None or not needs_parcel_crop(timeline):
            return timeline
        return {**timeline, "parcel_crop": await fetch_parcel_crop(tenant_id, parcel_urn)}
    except Exception as exc:  # noqa: BLE001 - the SDK returns None itself; this guards the unexpected
        logger.warning("crop timeline read failed tenant=%s parcel=%s: %s", tenant_id, parcel_urn, exc)
        return None


def continuous_modules(db: Session, tenant_id: str, parcel_urn: str) -> List[str]:
    """Ids of the modules that asked for continuous monitoring of the parcel."""
    rows = (
        db.query(VegetationContinuousRequest.module_id)
        .filter(VegetationContinuousRequest.tenant_id == tenant_id,
                VegetationContinuousRequest.entity_id == parcel_urn)
        .all()
    )
    return sorted(row[0] for row in rows)


def _request_filter(db: Session, tenant_id: str, parcel_urn: str, module_id: str):
    return db.query(VegetationContinuousRequest).filter(
        VegetationContinuousRequest.tenant_id == tenant_id,
        VegetationContinuousRequest.entity_id == parcel_urn,
        VegetationContinuousRequest.module_id == module_id,
    )


def add_continuous(db: Session, tenant_id: str, parcel_urn: str, module_id: str) -> None:
    """Record that a module asks for continuous monitoring. Idempotent."""
    if _request_filter(db, tenant_id, parcel_urn, module_id).first() is not None:
        return
    db.add(VegetationContinuousRequest(tenant_id=tenant_id, entity_id=parcel_urn, module_id=module_id))
    try:
        db.commit()
    except IntegrityError:  # a concurrent identical request won: same end state
        db.rollback()


def remove_continuous(db: Session, tenant_id: str, parcel_urn: str, module_id: str) -> None:
    """Withdraw a module's request. Idempotent."""
    _request_filter(db, tenant_id, parcel_urn, module_id).delete()
    db.commit()
