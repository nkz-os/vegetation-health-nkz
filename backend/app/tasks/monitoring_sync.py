"""Daily sync of each subscription with its parcel's crop (pause after harvest, resume, widen).

It only adjusts subscriptions that already exist: it never creates one. A pause by a
person (``paused_reason`` 'user', or no reason on an inactive row) is never undone here.
A failed crop read changes nothing for that parcel.
"""
import asyncio
import logging
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import get_db_session
from app.models import VegetationSubscription
from app.services.active_tenants import tenant_is_active
from app.services.monitoring import monitoring_period, utc_today
from app.services.monitoring_io import continuous_modules, fetch_timeline

logger = logging.getLogger(__name__)

MAX_CONCURRENT_READS = 5


def _restart_cursor(sub: VegetationSubscription) -> None:
    """Make the subscription due on the next pass and read its history from start_date."""
    sub.last_run_at = None
    sub.next_run_at = None


def _adjust(sub: VegetationSubscription, timeline: dict, continuous: List[str], today: date) -> str:
    """Apply the rules to one subscription (in memory). Returns paused | resumed | widened | unchanged."""
    period = monitoring_period(timeline, today, bool(continuous))
    if period is None:
        return "unchanged"
    if sub.is_active:
        if period.end is not None and period.end < today:
            sub.is_active = False
            sub.paused_reason = "harvest"
            return "paused"
        if sub.start_date > period.start:
            sub.start_date = period.start
            _restart_cursor(sub)
            return "widened"
        return "unchanged"
    if period.end is None:  # paused after a harvest and the period is open again
        sub.is_active = True
        sub.paused_reason = None
        sub.start_date = period.start
        _restart_cursor(sub)
        return "resumed"
    return "unchanged"


async def _read_timelines(keys: List[tuple]) -> Dict[tuple, Optional[dict]]:
    gate = asyncio.Semaphore(MAX_CONCURRENT_READS)

    async def read(tenant_id: str, parcel_urn: str) -> Optional[dict]:
        async with gate:
            return await fetch_timeline(tenant_id, parcel_urn)

    results = await asyncio.gather(*(read(t, p) for t, p in keys))
    return dict(zip(keys, results))


def sync_subscriptions(db: Session, today: date) -> Dict[str, int]:
    """Run the daily rules over every subscription; returns the counters."""
    counters = {"paused": 0, "resumed": 0, "widened": 0, "unchanged": 0, "errors": 0}
    subscriptions = (
        db.query(VegetationSubscription)
        .filter(or_(VegetationSubscription.is_active == True,  # noqa: E712
                    VegetationSubscription.paused_reason == "harvest"),
                tenant_is_active())
        .all()
    )
    # A pause by a person is never undone here, so its crop is not even read.
    todo = [s for s in subscriptions if s.is_active or s.paused_reason == "harvest"]
    counters["unchanged"] += len(subscriptions) - len(todo)
    keys = sorted({(s.tenant_id, s.entity_id) for s in todo})
    timelines = asyncio.run(_read_timelines(keys)) if keys else {}
    for sub in todo:
        timeline = timelines[(sub.tenant_id, sub.entity_id)]
        if timeline is None:
            logger.warning("monitoring sync: crop timeline unavailable tenant=%s parcel=%s; left as is",
                           sub.tenant_id, sub.entity_id)
            counters["errors"] += 1
            continue
        try:
            continuous = continuous_modules(db, sub.tenant_id, sub.entity_id)
            outcome = _adjust(sub, timeline, continuous, today)
            if outcome != "unchanged":
                db.commit()
                logger.info("monitoring sync: %s tenant=%s parcel=%s", outcome, sub.tenant_id, sub.entity_id)
        except Exception as exc:  # noqa: BLE001 - one parcel must not stop the others
            db.rollback()
            logger.error("monitoring sync failed tenant=%s parcel=%s: %s", sub.tenant_id, sub.entity_id, exc)
            counters["errors"] += 1
            continue
        counters[outcome] += 1
    return counters


@celery_app.task(name="vegetation.sync_monitoring")
def sync_monitoring() -> Dict[str, int]:
    """Beat task, before the subscription pass."""
    db = next(get_db_session())
    try:
        counters = sync_subscriptions(db, utc_today())
    finally:
        db.close()
    logger.info("monitoring sync done: %s", counters)
    return counters
