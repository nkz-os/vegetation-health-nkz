"""Per-parcel scene status: has THIS parcel its result for THIS scene?

Rules live here as pure functions; `record` writes with its own short session
so a failure never aborts the caller's transaction.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import database
from app.models.parcel_scenes import VegetationParcelScene

logger = logging.getLogger(__name__)

PENDING = "pending"
DONE = "done"
CLOUD_SKIPPED = "cloud_skipped"
NO_VALID_PIXELS = "no_valid_pixels"
FAILED = "failed"

MAX_ATTEMPTS = 3
STALE_PENDING = timedelta(hours=48)


def resolve_status(current: str | None, incoming: str) -> str:
    """A done scene never regresses; no_valid_pixels only replaces nothing or pending."""
    if current == DONE:
        return DONE
    if incoming == NO_VALID_PIXELS and current not in (None, PENDING):
        return current
    return incoming


def is_seen(status: str, attempts: int, updated_at: datetime, now: datetime) -> bool:
    """True when the planner must not enqueue this parcel/scene again."""
    if status in (DONE, CLOUD_SKIPPED, NO_VALID_PIXELS):
        return True
    if status == PENDING and now - updated_at < STALE_PENDING:
        return True
    return attempts >= MAX_ATTEMPTS


def scenes_seen(db, tenant_id: str, entity_id: str, scene_ids: list[str], now: datetime) -> set[str]:
    """Scene ids already handled for this parcel (reads with the task's session)."""
    if not scene_ids:
        return set()
    rows = db.query(VegetationParcelScene).filter(
        VegetationParcelScene.tenant_id == tenant_id,
        VegetationParcelScene.entity_id == entity_id,
        VegetationParcelScene.scene_id.in_(scene_ids),
    ).all()
    return {
        r.scene_id for r in rows
        if is_seen(r.status, r.attempts or 0, r.updated_at, now)
    }


def record(
    tenant_id: str,
    entity_id: str,
    scene_id: str,
    status: str,
    *,
    sensing_date: date | None = None,
    parcel_cloud_pct: float | None = None,
    job_id=None,
    new_attempt: bool = False,
) -> str:
    """Upsert the verdict of one parcel for one scene. Never raises; returns the stored status or ''."""
    if not entity_id or not scene_id:
        return ""
    session = None
    try:
        session = database.SessionLocal()
        session.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
        session.execute(
            pg_insert(VegetationParcelScene)
            .values(tenant_id=tenant_id, entity_id=entity_id, scene_id=scene_id, status=PENDING, attempts=0)
            .on_conflict_do_nothing(index_elements=["tenant_id", "entity_id", "scene_id"])
        )
        row = session.query(VegetationParcelScene).filter(
            VegetationParcelScene.tenant_id == tenant_id,
            VegetationParcelScene.entity_id == entity_id,
            VegetationParcelScene.scene_id == scene_id,
        ).with_for_update().one()
        row.status = resolve_status(row.status, status)
        if sensing_date is not None:
            row.sensing_date = sensing_date
        if parcel_cloud_pct is not None:
            row.parcel_cloud_pct = parcel_cloud_pct
        if job_id is not None:
            row.job_id = job_id
        if new_attempt:
            row.attempts = (row.attempts or 0) + 1
        row.updated_at = datetime.now(timezone.utc)
        stored = row.status
        session.commit()
        return stored
    except Exception as exc:
        logger.warning(
            "parcel scene status not recorded tenant=%s parcel=%s scene=%s status=%s: %s",
            tenant_id, entity_id, scene_id, status, exc,
        )
        return ""
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # closing must never raise either
                pass

