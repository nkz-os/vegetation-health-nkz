"""Tests for POST /api/vegetation/calculate engine routing + formula passthrough.

Covers the Task 6 wiring:
  (a) NDVI (eligible, no custom, SH usable)  -> Copernicus path, quota charged, kept
  (b) NDRE (red-edge, LOCAL_ONLY)            -> legacy local Celery dispatch, NO quota charge
  (c) custom-formula NDVI                    -> local, formula forwarded to the task
  (d) selector EngineDegradedException       -> reserve then release, falls back to local
  (e) quota check_and_reserve() False        -> HTTP 429 quota_exceeded, selector NOT called
  (f) SH NOT usable (no creds / degraded)    -> async local dispatch, selector + quota untouched
  (g) selector degraded_fallback result      -> reserve then release (net zero), job labeled local

Quota is reserved BEFORE the Sentinel Hub call (reserve-before ordering; see
FIX2 IMPORTANT-2 follow-up). An over-quota tenant is rejected with a 429 WITHOUT
any Sentinel Hub call (protecting the shared Copernicus credential). If the
compute degrades to the free local pipeline, or the selector errors, the
reserved unit is RELEASED so a degraded/failed run nets zero charge.

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


def _index_result(idx="NDVI", data_fidelity="sentinel_hub"):
    return IndexResult(
        index_type=idx, sensing_date=date(2026, 7, 20),
        mean=0.62, std=0.08, min=0.1, max=0.9,
        p10=0.4, p90=0.8, valid_pixels=1200, total_pixels=1500,
        data_fidelity=data_fidelity,
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
    quota.release.assert_not_called()  # genuine SH result — unit kept
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
    """(d) selector raising EngineDegradedException → reserve then release, then
    legacy local dispatch (the failed SH attempt consumed no genuine PU)."""
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.is_sentinel_hub_usable.return_value = True
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
    quota.check_and_reserve.assert_called_once_with(TENANT)
    quota.release.assert_called_once_with(TENANT)  # refund the failed reservation
    task.delay.assert_called_once()  # fell back rather than hard-failing


def test_quota_exceeded_returns_429():
    """(e) over quota (check_and_reserve() False) → HTTP 429 with ZERO Sentinel
    Hub calls.

    CONTRACT (FIX2, reverting FIX1's reserve-after): quota is reserved BEFORE
    the Sentinel Hub call. An over-quota tenant is rejected up front — the
    selector is NEVER awaited, so no Processing Unit is burned on the shared
    Copernicus credential. This is the whole point of the quota; the FIX1
    reserve-after ordering (which awaited the selector before the 429, burning
    a PU on every over-quota request) was wrong and is reverted here.
    """
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.is_sentinel_hub_usable.return_value = True
    selector.compute_indices = AsyncMock(return_value=[_index_result("NDVI")])
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
    quota.check_and_reserve.assert_called_once_with(TENANT)
    selector.compute_indices.assert_not_awaited()  # NO SH call — credential protected
    quota.release.assert_not_called()              # nothing reserved to refund
    task.delay.assert_not_called()


def test_sentinel_hub_not_usable_routes_async_local():
    """(f) NDVI Copernicus-eligible but NO usable SH credentials → the selector
    is never called, quota is never touched, and the request returns the normal
    async local dispatch {job_id} without blocking on an inline local compute."""
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.is_sentinel_hub_usable.return_value = False
    selector.compute_indices = AsyncMock()

    with patch.object(scenes, "SatelliteQuota") as QuotaCls, \
         patch.object(scenes, "calculate_vegetation_index") as task:
        task.delay.return_value = MagicMock(id="celery-task-f")
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
    selector.is_sentinel_hub_usable.assert_called_once_with(TENANT)
    selector.compute_indices.assert_not_awaited()  # never called the selector
    QuotaCls.assert_not_called()                    # no quota object constructed
    task.delay.assert_called_once()                 # async local Celery dispatch
    assert task.delay.call_args.kwargs.get("index_type") == "NDVI"


def test_degraded_fallback_result_not_charged_and_labeled_local():
    """(g) SH usable but selector returns a degraded_fallback result (runtime SH
    failure → ran on the local pipeline). With reserve-before, the unit IS
    reserved up front then RELEASED (net-zero charge), and the persisted job's
    engine must reflect local, not copernicus."""
    db = _make_db(download_bounds=GEOM)
    selector = MagicMock()
    selector.is_sentinel_hub_usable.return_value = True
    selector.compute_indices = AsyncMock(
        return_value=[_index_result("NDVI", data_fidelity="degraded_fallback")]
    )
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
    quota.check_and_reserve.assert_called_once_with(TENANT)  # reserved before SH call
    quota.release.assert_called_once_with(TENANT)  # refunded — ran local, no PU burned
    task.delay.assert_not_called()               # persisted as a completed job inline

    # The persisted VegetationJob must be labeled as having run local.
    persisted = db.add.call_args.args[0]
    assert persisted.result["engine"] == "local_processing"
    assert persisted.result["data_fidelity"] == "degraded_fallback"
    assert persisted.parameters["engine"] == "local_processing"
