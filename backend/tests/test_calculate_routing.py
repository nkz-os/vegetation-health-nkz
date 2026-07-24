"""Tests for POST /api/vegetation/calculate engine routing + formula passthrough.

Covers the Task 6 wiring:
  (a) NDVI (eligible, no custom)       -> EngineSelector Copernicus path, quota charged
  (b) NDRE (red-edge, LOCAL_ONLY)      -> legacy local Celery dispatch, NO quota charge
  (c) custom-formula NDVI              -> local, formula forwarded to the task
  (d) selector EngineDegradedException -> falls back to legacy local dispatch
  (e) quota check_and_reserve() False  -> HTTP 429 quota_exceeded

Plus pure unit tests for `route_index`.

Imports the real app (mirrors test_usage_api) — no module-level sys.modules
stubbing, so this file does not pollute sibling suites. DB and auth are
supplied via FastAPI dependency overrides; the selector, quota, and Celery
task are patched per-test.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Prevent FastAPI lifespan from opening a real DB connection at import.
with patch("app.database.init_db"):
    from app.main import app

import app.api.scenes as scenes
from app.api.scenes import get_db_with_tenant
from app.middleware.auth import require_auth
from app.engines.base import EngineDegradedException, IndexResult
from app.engines.routing import route_index

TENANT = "test-tenant-routing"
GEOM = {
    "type": "Polygon",
    "coordinates": [[[-1.6, 42.8], [-1.5, 42.8], [-1.5, 42.9], [-1.6, 42.9], [-1.6, 42.8]]],
}


# ---------------------------------------------------------------------------
# route_index — pure unit tests
# ---------------------------------------------------------------------------

class TestRouteIndex:
    def test_eligible_indices_go_copernicus(self):
        for idx in ("NDVI", "EVI", "SAVI", "GNDVI"):
            assert route_index(idx, has_custom_formula=False) == "copernicus"

    def test_ndre_stays_local(self):
        assert route_index("NDRE", has_custom_formula=False) == "local"

    def test_custom_formula_forces_local_even_for_eligible(self):
        assert route_index("NDVI", has_custom_formula=True) == "local"

    def test_unknown_index_defaults_local(self):
        assert route_index("VRA_ZONES", has_custom_formula=False) == "local"
        assert route_index("NDMI", has_custom_formula=False) == "local"


# ---------------------------------------------------------------------------
# Endpoint fixtures
# ---------------------------------------------------------------------------

def _make_db(download_bounds=None):
    """Mock Session. The parcel-bounds lookup returns a row whose
    `.parameters` carries `bounds`; add/commit are no-ops; refresh assigns
    an id (mirrors a real INSERT returning the PK)."""
    db = MagicMock()

    row = MagicMock()
    row.parameters = {"bounds": download_bounds} if download_bounds else {}
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
        row if download_bounds else None
    )

    def _refresh(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()

    db.refresh.side_effect = _refresh
    return db


def _client(db, selector=None):
    from fastapi.testclient import TestClient

    def _override_auth():
        return {"tenant_id": TENANT, "user_id": "u-1", "roles": ["User"]}

    def _override_db():
        yield db

    app.dependency_overrides[require_auth] = _override_auth
    app.dependency_overrides[get_db_with_tenant] = _override_db
    app.state.engine_selector = selector or MagicMock()
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def _index_result(idx="NDVI"):
    return IndexResult(
        index_type=idx, sensing_date=date(2026, 7, 20),
        mean=0.62, std=0.08, min=0.1, max=0.9,
        p10=0.4, p90=0.8, valid_pixels=1200, total_pixels=1500,
        data_fidelity="sentinel_hub",
    )


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

def test_ndvi_routes_to_copernicus_and_charges_quota():
    """(a) NDVI eligible + no custom formula → selector Copernicus path, quota charged."""
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.compute_indices = AsyncMock(return_value=[_index_result("NDVI")])

    quota = MagicMock()
    quota.check_and_reserve.return_value = True

    with patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch.object(scenes, "calculate_vegetation_index") as task:
        client = _client(db, selector)
        resp = client.post(
            "/api/vegetation/calculate",
            json={
                "index_type": "NDVI",
                "entity_id": "urn:ngsi-ld:AgriParcel:p1",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            },
        )

    assert resp.status_code == 200, resp.text
    assert "job_id" in resp.json()
    selector.compute_indices.assert_awaited_once()
    quota.check_and_reserve.assert_called_once_with(TENANT)
    task.delay.assert_not_called()  # Copernicus path enqueues no local task


def test_ndre_routes_local_no_quota_charge():
    """(b) NDRE (red-edge) → legacy local dispatch, quota never touched."""
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.compute_indices = AsyncMock()

    with patch.object(scenes, "SatelliteQuota") as QuotaCls, \
         patch.object(scenes, "calculate_vegetation_index") as task:
        task.delay.return_value = MagicMock(id="celery-task-1")
        client = _client(db, selector)
        resp = client.post(
            "/api/vegetation/calculate",
            json={
                "index_type": "NDRE",
                "entity_id": "urn:ngsi-ld:AgriParcel:p1",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            },
        )

    assert resp.status_code == 200, resp.text
    selector.compute_indices.assert_not_awaited()
    QuotaCls.assert_not_called()  # no quota object even constructed
    task.delay.assert_called_once()
    assert task.delay.call_args.kwargs.get("index_type") == "NDRE"


def test_custom_formula_ndvi_routes_local_formula_preserved():
    """(c) custom-formula NDVI → local, formula forwarded to the Celery task."""
    db = _make_db(download_bounds=GEOM)
    formula = "(B08 - B04) / (B08 + B04)"

    with patch.object(scenes, "SatelliteQuota") as QuotaCls, \
         patch.object(scenes, "calculate_vegetation_index") as task:
        task.delay.return_value = MagicMock(id="celery-task-2")
        client = _client(db)
        resp = client.post(
            "/api/vegetation/calculate",
            json={
                "index_type": "NDVI",
                "entity_id": "urn:ngsi-ld:AgriParcel:p1",
                "formula": formula,
                "formula_id": "f-123",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            },
        )

    assert resp.status_code == 200, resp.text
    QuotaCls.assert_not_called()
    task.delay.assert_called_once()
    assert task.delay.call_args.kwargs.get("formula") == formula


def test_selector_degraded_falls_back_to_local():
    """(d) selector raising EngineDegradedException → legacy local dispatch."""
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.compute_indices = AsyncMock(
        side_effect=EngineDegradedException("auth failed", retry_after_seconds=3600)
    )
    quota = MagicMock()
    quota.check_and_reserve.return_value = True

    with patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch.object(scenes, "calculate_vegetation_index") as task:
        task.delay.return_value = MagicMock(id="celery-task-3")
        client = _client(db, selector)
        resp = client.post(
            "/api/vegetation/calculate",
            json={
                "index_type": "NDVI",
                "entity_id": "urn:ngsi-ld:AgriParcel:p1",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            },
        )

    assert resp.status_code == 200, resp.text
    selector.compute_indices.assert_awaited_once()
    task.delay.assert_called_once()  # fell back rather than hard-failing


def test_quota_exceeded_returns_429():
    """(e) quota check_and_reserve() False → HTTP 429 quota_exceeded."""
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.compute_indices = AsyncMock()
    quota = MagicMock()
    quota.check_and_reserve.return_value = False
    quota.get_usage.return_value = {"used": 50, "limit": 50, "remaining": 0, "period": "2026-07"}

    with patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch.object(scenes, "calculate_vegetation_index") as task:
        client = _client(db, selector)
        resp = client.post(
            "/api/vegetation/calculate",
            json={
                "index_type": "NDVI",
                "entity_id": "urn:ngsi-ld:AgriParcel:p1",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            },
        )

    assert resp.status_code == 429, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "quota_exceeded"
    assert detail["used"] == 50
    assert detail["limit"] == 50
    selector.compute_indices.assert_not_awaited()
    task.delay.assert_not_called()
