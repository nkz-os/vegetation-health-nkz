"""Monitoring routes: user (gateway identity) and internal (shared secret), plus continuity requests."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import internal, parcels
from app.database import get_db_with_tenant
from app.models import VegetationContinuousRequest
from app.services import monitoring_candidates, monitoring_io
from app.services.observation_series import BrokerUnavailable

SHORT = "0123456789abcdef"
URN = f"urn:ngsi-ld:AgriParcel:{SHORT}"
STATE = {"crop": {"present": True}, "monitoring": "none", "needs_activation": True}
USER = {"X-Tenant-ID": "tenant-a", "X-User-ID": "u1"}
SECRET = {"X-Internal-Service-Secret": "s3"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(internal, "INTERNAL_SECRET", "s3")
    app = FastAPI()
    app.include_router(parcels.router)
    app.include_router(parcels.monitoring_router)
    app.include_router(internal.router)
    app.dependency_overrides[get_db_with_tenant] = lambda: "db"
    with TestClient(app) as c:
        yield c


@pytest.fixture
def state():
    with patch.object(parcels, "parcel_state", AsyncMock(return_value=dict(STATE))) as m, \
         patch.object(internal, "parcel_state", m):
        yield m


@pytest.fixture
def cands():
    value = [{"entity_id": URN, "name": "P"}]
    with patch.object(parcels, "candidates", AsyncMock(return_value=value)) as m, \
         patch.object(internal, "candidates", m):
        yield m


# ── user routes ──────────────────────────────────────────────────────────

def test_user_state_uses_the_tenant_from_auth(client, state):
    r = client.get(f"/api/vegetation/parcels/{SHORT}/monitoring", headers=USER)
    assert r.status_code == 200 and r.json() == STATE
    args = state.call_args.args
    assert args[0] == "db" and args[1] == "tenant-a" and args[2] == URN


def test_user_state_requires_identity(client, state):
    assert client.get(f"/api/vegetation/parcels/{SHORT}/monitoring").status_code in (400, 401)
    state.assert_not_called()


def test_user_state_is_502_when_the_broker_fails(client, state):
    state.side_effect = BrokerUnavailable("down")
    r = client.get(f"/api/vegetation/parcels/{SHORT}/monitoring", headers=USER)
    assert r.status_code == 502 and "needs_activation" not in r.text


def test_user_candidates(client, cands):
    r = client.get("/api/vegetation/monitoring/candidates", headers=USER)
    assert r.status_code == 200
    assert r.json() == {"tenant_id": "tenant-a", "candidates": [{"entity_id": URN, "name": "P"}]}
    assert cands.call_args.args[:2] == ("db", "tenant-a")


def test_user_candidates_502(client, cands):
    cands.side_effect = BrokerUnavailable("down")
    assert client.get("/api/vegetation/monitoring/candidates", headers=USER).status_code == 502


# ── internal routes ──────────────────────────────────────────────────────

STATE_URL = f"/api/internal/vegetation/parcels/{SHORT}/monitoring"
CANDS_URL = "/api/internal/vegetation/monitoring/candidates"


@pytest.fixture
def session():
    db = MagicMock(name="session")
    with patch.object(internal, "SessionLocal", return_value=db):
        yield db


@pytest.mark.parametrize("url", [STATE_URL, CANDS_URL])
def test_internal_reads_need_the_secret(client, state, cands, session, url):
    assert client.get(url, params={"tenant_id": "tenant-a"}).status_code == 401
    assert client.get(url, params={"tenant_id": "tenant-a"}, headers={"X-Internal-Service-Secret": "no"}).status_code == 401
    r = client.get(url, params={"tenant_id": "tenant-a"}, headers={"X-Internal-Service-Secret": "ñ".encode("utf-8")})
    assert r.status_code == 401
    state.assert_not_called()
    cands.assert_not_called()


@pytest.mark.parametrize("url", [STATE_URL, CANDS_URL])
def test_internal_reads_need_a_tenant(client, state, cands, session, url):
    assert client.get(url, headers=SECRET).status_code == 400


def test_internal_state(client, state, session):
    r = client.get(STATE_URL, params={"tenant_id": "tenant-a"}, headers=SECRET)
    assert r.status_code == 200 and r.json() == STATE
    assert state.call_args.args[1:3] == ("tenant-a", URN)
    session.close.assert_called_once()


def test_internal_candidates(client, cands, session):
    r = client.get(CANDS_URL, params={"tenant_id": "tenant-a"}, headers=SECRET)
    assert r.status_code == 200 and r.json()["tenant_id"] == "tenant-a" and len(r.json()["candidates"]) == 1
    session.close.assert_called_once()


def test_internal_reads_are_502_when_the_broker_fails(client, state, cands, session):
    state.side_effect = BrokerUnavailable("down")
    cands.side_effect = BrokerUnavailable("down")
    assert client.get(STATE_URL, params={"tenant_id": "tenant-a"}, headers=SECRET).status_code == 502
    assert client.get(CANDS_URL, params={"tenant_id": "tenant-a"}, headers=SECRET).status_code == 502
    assert session.close.call_count == 2


# ── continuity requests ──────────────────────────────────────────────────

CONT_URL = f"/api/internal/vegetation/parcels/{SHORT}/continuous"
BODY = {"tenant_id": "tenant-a", "module_id": "carbon"}


class RequestsDb:
    """Keeps continuity requests as a set, with the session surface the helpers use."""

    def __init__(self, rows=()):
        self.rows = set(rows)
        self.commits = 0
        self.closed = False
        self._filters = None

    def query(self, model):
        assert model is VegetationContinuousRequest
        return self

    def filter(self, *clauses):
        self._clauses = clauses
        return self

    def _key(self):
        values = {c.left.key: c.right.value for c in self._clauses}
        return (values["tenant_id"], values["entity_id"], values["module_id"])

    def first(self):
        return SimpleNamespace() if self._key() in self.rows else None

    def delete(self):
        existed = self._key() in self.rows
        self.rows.discard(self._key())
        return int(existed)

    def add(self, row):
        self.rows.add((row.tenant_id, row.entity_id, row.module_id))

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def requests_db():
    db = RequestsDb()
    with patch.object(internal, "SessionLocal", return_value=db), \
         patch.object(internal, "invalidate_candidates") as invalidate:
        db.invalidate = invalidate
        yield db


def test_continuous_requires_the_secret(client, requests_db):
    assert client.post(CONT_URL, json=BODY).status_code == 401
    assert client.request("DELETE", CONT_URL, json=BODY).status_code == 401
    assert requests_db.rows == set()


@pytest.mark.parametrize("body", [{"tenant_id": "tenant-a"}, {"module_id": "carbon"},
                                  {"tenant_id": "", "module_id": "carbon"}, {"tenant_id": "tenant-a", "module_id": ""}])
def test_continuous_rejects_a_bad_body(client, requests_db, body):
    assert client.post(CONT_URL, json=body, headers=SECRET).status_code == 422
    assert client.request("DELETE", CONT_URL, json=body, headers=SECRET).status_code == 422


def test_post_records_the_request_idempotently_and_drops_the_cache(client, requests_db):
    for _ in range(2):
        r = client.post(CONT_URL, json=BODY, headers=SECRET)
        assert r.status_code == 204 and r.content == b""
    assert requests_db.rows == {("tenant-a", URN, "carbon")}
    assert requests_db.commits == 1 and requests_db.closed
    requests_db.invalidate.assert_called_with("tenant-a")


def test_delete_removes_the_request_idempotently_and_drops_the_cache(client, requests_db):
    requests_db.rows.add(("tenant-a", URN, "carbon"))
    requests_db.rows.add(("tenant-a", URN, "other"))
    for _ in range(2):
        r = client.request("DELETE", CONT_URL, json=BODY, headers=SECRET)
        assert r.status_code == 204
    assert requests_db.rows == {("tenant-a", URN, "other")}
    requests_db.invalidate.assert_called_with("tenant-a")


# ── activation drops the candidates cache ────────────────────────────────

@pytest.mark.parametrize("action", ["activate", "deactivate", "teardown"])
def test_setup_parcel_invalidates_the_candidates_cache(client, action):
    with patch.object(internal, "SessionLocal", return_value=MagicMock()), \
         patch.object(internal, "fetch_timeline", AsyncMock(return_value=None)), \
         patch.object(internal, "_fetch_parcel_location", AsyncMock(return_value=(None, None, None))), \
         patch.object(internal, "_deactivate_subscription", return_value=False), \
         patch.object(internal, "invalidate_candidates") as invalidate:
        r = client.post("/api/internal/setup-parcel", headers=SECRET,
                        json={"parcel_id": URN, "tenant_id": "tenant-a", "action": action})
    assert r.status_code == 201
    invalidate.assert_called_once_with("tenant-a")
