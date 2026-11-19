"""Reads that feed the monitoring rules: the parcel's crop timeline and the modules asking for continuity."""
import logging
from typing import List, Optional

from nkz_platform_sdk.crop_timeline import fetch_crop_timeline
from sqlalchemy.orm import Session

from app.models import VegetationContinuousRequest

logger = logging.getLogger(__name__)


async def fetch_timeline(tenant_id: str, parcel_urn: str) -> Optional[dict]:
    """The parcel's crop timeline, or None when the broker could not be read.

    None is a failed read, never "no crop": a parcel without a crop has a timeline
    with no current or previous cycle. The caller must then change nothing.
    """
    try:
        return await fetch_crop_timeline(tenant_id, parcel_urn)
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
