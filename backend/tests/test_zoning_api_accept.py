"""Accepting a zoning: the current one keeps its ids; a pending one is published (real Postgres, fake broker)."""
from datetime import datetime, timezone

import pytest

from app.jobs.zoning_multidate.actions import _enqueue_backfill as REAL_ENQUEUE  # before the fixture stubs it
from tests.zoning_fakes import EAST, NKZ, PARCEL, TENANT, FakeBroker, run

A, B, C = "a" * 32, "b" * 32, "c" * 32
ACCEPTED = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
URL = f"/api/vegetation/zoning/{PARCEL}"


@pytest.fixture
def api(zoning_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import zoning_versions
    from app.jobs.zoning_multidate import actions
    from app.database import get_db_with_tenant
    from app.main import app
    from app.middleware.auth import require_auth

    broker = FakeBroker()
    monkeypatch.setattr(zoning_versions, "broker_for", lambda tenant_id: broker)
    monkeypatch.setattr(actions, "_enqueue_backfill", lambda tenant_id, parcel_id: None)  # no Celery broker here

    def _db():
        yield zoning_db()

    as_roles(["TechnicalConsultant"])
    app.dependency_overrides[get_db_with_tenant] = _db
    yield TestClient(app), broker, zoning_db
    app.dependency_overrides.clear()


def as_roles(roles):
    """Make the test user carry ``roles`` (the gateway sends them in X-User-Roles)."""
    from app.main import app
    from app.middleware.auth import require_auth
    app.dependency_overrides[require_auth] = lambda: {"tenant_id": TENANT, "user_id": "u-1", "roles": roles}


def _seed(db_factory, *, current=None, accepted=False, pendings=(), current_entities=None):
    from app.jobs.zoning_multidate import versions as v
    db = db_factory()
    with v.parcel_lock(db, TENANT, PARCEL):
        if current:
            entities = current_entities if current_entities is not None else run(current)
            v.record_current(db, TENANT, PARCEL, current, entities, source="proposal", index_type="NDVI",
                             accepted_at=ACCEPTED if accepted else None, accepted_by="u0" if accepted else None)
        for run_id, entities, reason in pendings:
            v.record_pending(db, TENANT, PARCEL, run_id, entities, index_type="NDVI",
                             outcome_reason=reason, job_id="j", parked_on_run_id=current)
    db.close()


def _rows(db_factory):
    from app.models import ZoningVersion
    db = db_factory()
    return {r.run_id: r for r in db.query(ZoningVersion).all()}


def test_accept_current_keeps_ids(api):
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A)
    r = client.post(f"{URL}/accept", json={"run_id": A})
    assert r.status_code == 200, r.text
    assert set(broker.store) == {e["id"] for e in run(A)}
    assert not broker.created and not broker.deleted
    assert all("acceptedAt" in e for e in broker.store.values())
    row = _rows(dbf)[A]
    assert (row.status, row.accepted_by) == ("current", "u-1") and row.accepted_at is not None
    assert all("acceptedAt" in e for e in row.entities)
    assert r.json()["run_id"] == A and r.json()["status"] == "current"


def test_accept_is_idempotent(api):
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    r = client.post(f"{URL}/accept", json={"run_id": A})
    assert r.status_code == 200 and not broker.appended
    assert _rows(dbf)[A].accepted_by == "u0"


def test_accept_backfills_pre_p2_run(api):
    client, broker, dbf = api
    # As the broker returns them: the relationship under its compacted alias, an nkz term as a full IRI.
    for e in run(A):
        e["refAgriParcel"] = e.pop("hasAgriParcel")
        e[NKZ + "zoningMethod"] = e.pop("zoningMethod")
        e["@context"] = ["http://context/ngsi-ld-context.json"]
        broker.store[e["id"]] = e
    r = client.post(f"{URL}/accept", json={"run_id": A})
    assert r.status_code == 200, r.text
    row = _rows(dbf)[A]
    assert row.status == "current" and row.accepted_at is not None and row.index_type == "NDVI"
    for e in row.entities:
        assert e["hasAgriParcel"]["object"] == PARCEL and "refAgriParcel" not in e and "@context" not in e
        assert e["zoningMethod"]["value"] == "vegetationIndexMultiDate"
    assert len(row.entities) == 2


def test_accept_unknown_run_is_404(api):
    client, _, _ = api
    assert client.post(f"{URL}/accept", json={"run_id": A}).status_code == 404


def test_accept_pending_publishes_and_retires(api):
    from app.models import ZoningNote
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    db = dbf()
    db.add(ZoningNote(tenant_id=TENANT, parcel_id=PARCEL, zone_id=run(A)[1]["id"], geometry=EAST,
                      cause="waterlogging", created_by="u0"))
    db.commit()
    r = client.post(f"{URL}/accept", json={"run_id": B})
    assert r.status_code == 200, r.text
    assert broker.run_ids() == {B}
    assert all("acceptedAt" in e for e in broker.store.values())
    low = broker.store[run(B)[1]["id"]]
    assert low["technicianCause"]["value"] == "waterlogging"
    rows = _rows(dbf)
    assert (rows[A].status, rows[B].status) == ("superseded", "current")
    assert rows[B].accepted_by == "u-1"


def test_accept_stale_run_is_409(api):
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None), (C, run(C), None)])
    r = client.post(f"{URL}/accept", json={"run_id": B})  # B was discarded by the newer C
    assert r.status_code == 409
    assert broker.run_ids() == {A} and _rows(dbf)[C].status == "pending"


def test_accept_no_zones_pending_is_409(api):
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, [], "homogeneous")])
    assert client.post(f"{URL}/accept", json={"run_id": B}).status_code == 409
    assert broker.run_ids() == {A}


def test_broker_failure_leaves_tables_unchanged(api):
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    broker.refuse_create = True
    r = client.post(f"{URL}/accept", json={"run_id": B})
    assert r.status_code == 502
    rows = _rows(dbf)
    assert (rows[A].status, rows[B].status) == ("current", "pending")
    assert broker.run_ids() == {A}


def test_broker_failure_on_accept_current_keeps_it_unaccepted(api):
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A)
    broker.refuse_append = True
    assert client.post(f"{URL}/accept", json={"run_id": A}).status_code == 502
    assert _rows(dbf)[A].accepted_at is None


def test_versions_list_order_and_shape(api):
    client, broker, dbf = api
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    r = client.get(f"{URL}/versions")
    assert r.status_code == 200
    body = r.json()
    assert [v["run_id"] for v in body] == [B, A]
    a = body[1]
    assert a["status"] == "current" and a["source"] == "proposal" and a["accepted_at"]
    assert a["levels"] == [{"level": "high", "area_ha": 1.0, "relative_pct": 112},
                           {"level": "low", "area_ha": 1.0, "relative_pct": 88}]
    for key in ("derived_from", "index_type", "outcome_reason", "created_at"):
        assert key in a


def test_accept_current_sweeps_leftovers(api):
    from tests.zoning_fakes import zone, WEST
    client, broker, dbf = api
    leftover = zone("z" * 32, 2, "medium", WEST)
    broker.store.update({e["id"]: e for e in run(A) + [leftover]})
    _seed(dbf, current=A)
    assert client.post(f"{URL}/accept", json={"run_id": A}).status_code == 200
    assert broker.run_ids() == {A}


def _queue_down(monkeypatch):
    """The real enqueue, with the Celery broker refusing the task."""
    from app.jobs.zoning_multidate import actions
    from app.tasks import processing_tasks

    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise ConnectionError("queue down")

    monkeypatch.setattr(actions, "_enqueue_backfill", REAL_ENQUEUE)
    monkeypatch.setattr(processing_tasks.backfill_current_zone_products_task, "delay", boom)
    return calls


def test_accept_succeeds_when_the_backfill_cannot_be_queued(api, monkeypatch, caplog):
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    calls = _queue_down(monkeypatch)
    r = client.post(f"{URL}/accept", json={"run_id": B})
    assert r.status_code == 200, r.text
    assert calls  # the enqueue was attempted and failed
    assert broker.run_ids() == {B}
    assert _rows(dbf)[B].status == "current"
    assert "zone backfill not queued" in caplog.text
