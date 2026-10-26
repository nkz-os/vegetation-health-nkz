"""Activating the module on a parcel owns its monitoring subscription (the beat's
only input); deactivating pauses it and keeps the data."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import internal
from app.models import VegetationSubscription

URL = "/api/internal/setup-parcel"
HEADERS = {"X-Internal-Service-Secret": "s3"}
PARCEL = "urn:ngsi-ld:AgriParcel:p1"
GEOMETRY = {"type": "Polygon", "coordinates": [[[-2.0, 42.0], [-2.0, 42.1], [-1.9, 42.1], [-2.0, 42.0]]]}


class FakeSession:
    """Just enough of a SQLAlchemy session for the subscription helpers."""

    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.commits = 0
        self.closed = False

    def query(self, _model):
        return self

    def filter(self, *_args):
        return self

    def first(self):
        return self.existing

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_ensure_creates_a_weekly_subscription_with_backfill():
    db = FakeSession()
    assert internal._ensure_subscription(db, "acme", PARCEL, GEOMETRY) == "created"
    (sub,) = db.added
    assert isinstance(sub, VegetationSubscription)
    assert (sub.tenant_id, sub.entity_id, sub.entity_type) == ("acme", PARCEL, "AgriParcel")
    assert sub.geometry.startswith("SRID=4326;MULTIPOLYGON")
    assert sub.start_date == date.today() - timedelta(days=internal.SUBSCRIPTION_BACKFILL_DAYS)
    assert sub.index_types == list(internal.SUBSCRIPTION_INDEX_TYPES)
    assert (sub.frequency, sub.is_active, sub.status) == ("weekly", True, "created")
    assert db.commits == 1


def test_ensure_leaves_an_active_subscription_alone():
    db = FakeSession(existing=VegetationSubscription(tenant_id="acme", entity_id=PARCEL, is_active=True))
    assert internal._ensure_subscription(db, "acme", PARCEL, GEOMETRY) == "unchanged"
    assert db.added == [] and db.commits == 0


def test_ensure_reactivates_a_paused_subscription_and_makes_it_due():
    sub = VegetationSubscription(tenant_id="acme", entity_id=PARCEL, is_active=False, next_run_at=date(2030, 1, 1))
    db = FakeSession(existing=sub)
    assert internal._ensure_subscription(db, "acme", PARCEL, GEOMETRY) == "reactivated"
    assert sub.is_active is True and sub.next_run_at is None and db.commits == 1


def test_deactivate_pauses_only_an_active_subscription():
    sub = VegetationSubscription(tenant_id="acme", entity_id=PARCEL, is_active=True)
    db = FakeSession(existing=sub)
    assert internal._deactivate_subscription(db, "acme", PARCEL) is True
    assert sub.is_active is False and db.commits == 1
    assert internal._deactivate_subscription(FakeSession(existing=None), "acme", PARCEL) is False
    assert internal._deactivate_subscription(db, "acme", PARCEL) is False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(internal, "INTERNAL_SECRET", "s3")
    app = FastAPI()
    app.include_router(internal.router)
    with TestClient(app) as c:
        yield c


def test_activate_sets_up_the_subscription_and_closes_the_session(client):
    db = FakeSession()
    with patch.object(internal, "SessionLocal", return_value=db), \
         patch.object(internal, "_fetch_parcel_location", AsyncMock(return_value=(GEOMETRY, 42.05, -1.97))), \
         patch.object(internal, "_enqueue_lst"):
        r = client.post(URL, json={"parcel_id": PARCEL, "tenant_id": "acme"}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["subscription"] == "created"
    assert len(db.added) == 1 and db.closed


def test_activate_without_geometry_skips_the_subscription(client):
    session = MagicMock()
    with patch.object(internal, "SessionLocal", session), \
         patch.object(internal, "_fetch_parcel_location", AsyncMock(return_value=(None, None, None))), \
         patch.object(internal, "_enqueue_lst"):
        r = client.post(URL, json={"parcel_id": PARCEL, "tenant_id": "acme"}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["subscription"] == "skipped"
    session.assert_not_called()


def test_activate_reports_a_subscription_error_but_still_answers_201(client):
    with patch.object(internal, "SessionLocal", side_effect=RuntimeError("db down")), \
         patch.object(internal, "_fetch_parcel_location", AsyncMock(return_value=(GEOMETRY, 42.05, -1.97))), \
         patch.object(internal, "_enqueue_lst"):
        r = client.post(URL, json={"parcel_id": PARCEL, "tenant_id": "acme"}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["subscription"] == "error"


@pytest.mark.parametrize("action,message", [("deactivate", "deactivated"), ("teardown", "torn_down")])
def test_deactivate_and_teardown_pause_the_subscription(client, action, message):
    sub = VegetationSubscription(tenant_id="acme", entity_id=PARCEL, is_active=True)
    db = FakeSession(existing=sub)
    with patch.object(internal, "SessionLocal", return_value=db), \
         patch.object(internal, "_fetch_parcel_location", AsyncMock()) as location:
        r = client.post(URL, json={"parcel_id": PARCEL, "tenant_id": "acme", "action": action}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json() == {"message": message, "parcel_id": PARCEL, "subscription_paused": True}
    assert sub.is_active is False and db.closed
    location.assert_not_awaited()
