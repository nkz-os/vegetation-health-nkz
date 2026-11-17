"""Zoning version repository and the per-parcel lock.

The transition and contention tests need a real Postgres (partial unique
indexes, advisory locks): they run when ZONING_PG_URL is set (CI sets it)
and are skipped otherwise.
"""
import threading
import time
from datetime import datetime, timezone

import pytest

T, P = "acme", "urn:ngsi-ld:AgriParcel:p1"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


class _FakeDb:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append(("execute", str(stmt), dict(params or {})))

    def commit(self):
        self.calls.append(("commit",))

    def rollback(self):
        self.calls.append(("rollback",))


def test_lock_released_on_exception():
    from app.jobs.zoning_multidate.versions import parcel_lock
    db = _FakeDb()
    with pytest.raises(RuntimeError):
        with parcel_lock(db, T, P):
            raise RuntimeError("broker down")
    assert "pg_advisory_xact_lock" in db.calls[0][1]
    assert db.calls[-1] == ("rollback",)
    assert ("commit",) not in db.calls


def test_lock_commits_on_success():
    from app.jobs.zoning_multidate.versions import parcel_lock
    db = _FakeDb()
    with parcel_lock(db, T, P):
        pass
    assert db.calls[-1] == ("commit",)


def test_lock_key_is_per_tenant_and_parcel():
    from app.jobs.zoning_multidate.versions import lock_key
    keys = {lock_key("acme", P), lock_key("tenant-b", P), lock_key("acme", "urn:ngsi-ld:AgriParcel:p2")}
    assert len(keys) == 3


# ---------------------------------------------------------------- real Postgres

# Real Postgres: the `zoning_db` fixture (conftest) skips without ZONING_PG_URL.


def _entities(run_id):
    return [{"id": f"urn:ngsi-ld:AgriManagementZone:p1:{run_id[:8]}:L1", "type": "AgriManagementZone"}]


def _statuses(db):
    from app.models import ZoningVersion
    return {v.run_id: v.status for v in db.query(ZoningVersion).all()}


def test_record_current_supersedes_previous(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    db = zoning_db()
    with v.parcel_lock(db, T, P):
        v.record_current(db, T, P, "run-a", _entities("run-a"), source="proposal", index_type="NDVI")
    with v.parcel_lock(db, T, P):
        v.record_current(db, T, P, "run-b", _entities("run-b"), source="proposal", index_type="NDVI")
    assert _statuses(db) == {"run-a": "superseded", "run-b": "current"}
    assert v.current(db, T, P).run_id == "run-b"
    assert v.current(db, "tenant-b", P) is None


def test_record_pending_discards_previous_pending(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    db = zoning_db()
    with v.parcel_lock(db, T, P):
        v.record_current(db, T, P, "run-a", _entities("run-a"), source="proposal", index_type="NDVI",
                         accepted_at=NOW, accepted_by="u1")
        v.record_pending(db, T, P, "run-b", _entities("run-b"), index_type="NDVI", outcome_reason=None, job_id="j1",
                         parked_on_run_id="run-a")
    with v.parcel_lock(db, T, P):
        v.record_pending(db, T, P, "run-c", [], index_type="NDVI", outcome_reason="homogeneous", job_id="j2",
                         parked_on_run_id="run-a")
    assert _statuses(db) == {"run-a": "current", "run-b": "discarded", "run-c": "pending"}
    assert v.pending(db, T, P).outcome_reason == "homogeneous"


def test_merge_record_discards_pending(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.models import ZoningVersion
    db = zoning_db()
    with v.parcel_lock(db, T, P):
        v.record_current(db, T, P, "run-a", _entities("run-a"), source="proposal", index_type="NDVI",
                         accepted_at=NOW, accepted_by="u1")
        v.record_pending(db, T, P, "run-b", _entities("run-b"), index_type="NDVI", outcome_reason=None, job_id="j1",
                         parked_on_run_id="run-a")
    with v.parcel_lock(db, T, P):
        v.record_current(db, T, P, "run-c", _entities("run-c"), source="merge", index_type="NDVI",
                         derived_from="run-a", accepted_at=NOW, accepted_by="u1")
    assert _statuses(db) == {"run-a": "superseded", "run-b": "discarded", "run-c": "current"}
    assert db.get(ZoningVersion, "run-b").outcome_reason == "superseded_by_merge"


def test_promote_pending_and_mark_accepted(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    db = zoning_db()
    with v.parcel_lock(db, T, P):
        v.record_current(db, T, P, "run-a", _entities("run-a"), source="proposal", index_type="NDVI")
        v.mark_accepted(db, v.current(db, T, P), "u1", NOW)
        v.record_pending(db, T, P, "run-b", _entities("run-b"), index_type="NDVI", outcome_reason=None, job_id="j1",
                         parked_on_run_id="run-a")
    with v.parcel_lock(db, T, P):
        v.promote_pending(db, v.pending(db, T, P), accepted_by="u2", accepted_at=NOW)
    assert _statuses(db) == {"run-a": "superseded", "run-b": "current"}
    cur = v.current(db, T, P)
    assert (cur.accepted_by, cur.accepted_at) == ("u2", NOW)
    assert v.pending(db, T, P) is None


def test_supersede_current_leaves_no_current(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    db = zoning_db()
    with v.parcel_lock(db, T, P):
        v.record_current(db, T, P, "run-a", _entities("run-a"), source="proposal", index_type="NDVI")
    with v.parcel_lock(db, T, P):
        v.supersede_current(db, T, P)
    assert v.current(db, T, P) is None


def test_lock_serialises_two_sessions(zoning_db):
    """A second action on the parcel waits until the first one commits."""
    from app.jobs.zoning_multidate import versions as v
    events = []
    first_in = threading.Event()

    def first():
        db = zoning_db()
        with v.parcel_lock(db, T, P):
            events.append("first-in")
            first_in.set()
            time.sleep(0.5)
            v.record_current(db, T, P, "run-a", _entities("run-a"), source="proposal", index_type="NDVI")
            events.append("first-out")
        db.close()

    def second():
        first_in.wait(5)
        db = zoning_db()
        with v.parcel_lock(db, T, P):
            events.append("second-in")
            # Reads inside the lock see what the first action committed.
            prev = v.current(db, T, P)
            events.append(f"saw-{prev.run_id if prev else None}")
            v.record_current(db, T, P, "run-b", _entities("run-b"), source="proposal", index_type="NDVI")
        db.close()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert events == ["first-in", "first-out", "second-in", "saw-run-a"]
    assert _statuses(zoning_db()) == {"run-a": "superseded", "run-b": "current"}


def test_other_parcel_is_not_blocked(zoning_db):
    from sqlalchemy import text

    from app.jobs.zoning_multidate import versions as v
    a, b = zoning_db(), zoning_db()
    with v.parcel_lock(a, T, P):
        got = b.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                        {"k": v.lock_key(T, "urn:ngsi-ld:AgriParcel:p2")}).scalar()
        same = b.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                         {"k": v.lock_key(T, P)}).scalar()
        b.rollback()
    assert (got, same) == (True, False)
