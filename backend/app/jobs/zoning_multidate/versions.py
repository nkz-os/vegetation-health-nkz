"""Zoning versions of a parcel: the current one (published), a pending proposal, history.

At most one ``current`` and one ``pending`` per parcel (partial unique indexes,
migration 016). Every change goes through ``parcel_lock`` so a proposal, an
acceptance and a merge on the same parcel never interleave.

Queries filter ``tenant_id`` explicitly: the worker does not set the RLS
tenant setting, so row-level security cannot be relied on here.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import text

from app.models.zoning import ZoningNote, ZoningVersion

_LOCK = text("SELECT pg_advisory_xact_lock(hashtext(:key))")


def lock_key(tenant_id: str, parcel_id: str) -> str:
    return f"zoning:{tenant_id}:{parcel_id}"


@contextmanager
def parcel_lock(db, tenant_id: str, parcel_id: str) -> Iterator[None]:
    """Run the block as one transaction holding the parcel's zoning lock.

    Transaction-scoped (released by the commit or rollback that closes the
    block), so it cannot leak when a pooled session changes connection.
    Commits on success, rolls back on any error. Work the session had not
    committed before entering is committed or rolled back with the block.
    """
    db.execute(_LOCK, {"key": lock_key(tenant_id, parcel_id)})
    try:
        yield
    except BaseException:
        db.rollback()
        raise
    db.commit()


def _with_status(db, tenant_id: str, parcel_id: str, status: str) -> ZoningVersion | None:
    return (
        db.query(ZoningVersion)
        .filter(ZoningVersion.tenant_id == tenant_id,
                ZoningVersion.parcel_id == parcel_id,
                ZoningVersion.status == status)
        .one_or_none()
    )


def current(db, tenant_id: str, parcel_id: str) -> ZoningVersion | None:
    return _with_status(db, tenant_id, parcel_id, "current")


def pending(db, tenant_id: str, parcel_id: str) -> ZoningVersion | None:
    return _with_status(db, tenant_id, parcel_id, "pending")


def by_run(db, tenant_id: str, parcel_id: str, run_id: str) -> ZoningVersion | None:
    return (
        db.query(ZoningVersion)
        .filter(ZoningVersion.tenant_id == tenant_id,
                ZoningVersion.parcel_id == parcel_id,
                ZoningVersion.run_id == run_id)
        .one_or_none()
    )


def history(db, tenant_id: str, parcel_id: str) -> list[ZoningVersion]:
    return (
        db.query(ZoningVersion)
        .filter(ZoningVersion.tenant_id == tenant_id, ZoningVersion.parcel_id == parcel_id)
        .order_by(ZoningVersion.created_at.desc())
        .all()
    )


def _now() -> datetime:
    # Wall clock, not the transaction start: versions recorded in one transaction keep their order.
    return datetime.now(timezone.utc)


def _retire(db, version: ZoningVersion | None, status: str) -> None:
    # Flushed on its own: SQLAlchemy writes INSERTs before UPDATEs, and the new
    # row would otherwise collide with this one on the partial unique index.
    if version is not None:
        version.status = status
        db.flush()


def supersede_current(db, tenant_id: str, parcel_id: str) -> None:
    """The current zoning left the broker (retired with no replacement)."""
    _retire(db, current(db, tenant_id, parcel_id), "superseded")


def record_current(db, tenant_id: str, parcel_id: str, run_id: str, entities: list[dict], *,
                   source: str, index_type: str | None, derived_from: str | None = None,
                   job_id: str | None = None, accepted_at: datetime | None = None,
                   accepted_by: str | None = None) -> ZoningVersion:
    """A run just published to the broker becomes the current version."""
    _retire(db, current(db, tenant_id, parcel_id), "superseded")
    if source == "proposal":
        # A proposal published directly is newer evidence than any pending one.
        _retire(db, pending(db, tenant_id, parcel_id), "discarded")
    version = ZoningVersion(
        run_id=run_id, tenant_id=tenant_id, parcel_id=parcel_id, status="current", source=source,
        derived_from=derived_from, index_type=index_type, entities=entities, job_id=job_id,
        accepted_at=accepted_at, accepted_by=accepted_by, created_at=_now(),
    )
    db.add(version)
    db.flush()
    return version


def record_pending(db, tenant_id: str, parcel_id: str, run_id: str, entities: list[dict], *,
                   index_type: str | None, outcome_reason: str | None,
                   job_id: str | None) -> ZoningVersion:
    """A proposal kept aside because the current zoning was accepted (entities may be empty)."""
    _retire(db, pending(db, tenant_id, parcel_id), "discarded")
    version = ZoningVersion(
        run_id=run_id, tenant_id=tenant_id, parcel_id=parcel_id, status="pending", source="proposal",
        index_type=index_type, outcome_reason=outcome_reason, entities=entities, job_id=job_id,
        created_at=_now(),
    )
    db.add(version)
    db.flush()
    return version


def promote_pending(db, version: ZoningVersion, *, accepted_by: str, accepted_at: datetime) -> None:
    """The pending proposal, now published, becomes the current version, accepted."""
    _retire(db, current(db, version.tenant_id, version.parcel_id), "superseded")
    version.status = "current"
    version.accepted_at, version.accepted_by = accepted_at, accepted_by
    db.flush()


def mark_accepted(db, version: ZoningVersion, user_id: str, at: datetime) -> None:
    version.accepted_at, version.accepted_by = at, user_id
    db.flush()


def notes_of(db, tenant_id: str, parcel_id: str) -> list[ZoningNote]:
    return (
        db.query(ZoningNote)
        .filter(ZoningNote.tenant_id == tenant_id, ZoningNote.parcel_id == parcel_id)
        .order_by(ZoningNote.created_at)
        .all()
    )
