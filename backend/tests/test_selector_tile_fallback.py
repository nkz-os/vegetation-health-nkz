"""Tests for Task 8: selector tile-fallback degradation recording +
typed `TileLocalFallback` exception (replacing the bare `NotImplementedError`
control flow that `api/tiles.py` used to rely on).

Covers:
  (a) SentinelHub tile failure -> tenant recorded in `_tenant_degraded`
      (map + TTL) AND `TileLocalFallback` is raised (not `NotImplementedError`,
      not a returned fallback tile).
  (b) A subsequent tile call for the same degraded tenant short-circuits to
      the fallback engine (never re-hits the tenant's primary SentinelHub
      engine) and still raises `TileLocalFallback`.
  (c) `api/tiles.py`'s `/sentinel-hub/...` endpoint catches `TileLocalFallback`
      explicitly and falls through to the local-COG lookup (step 3), instead
      of the exception propagating as an unhandled error.
"""

import logging
import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.engines.selector import EngineSelector
from app.engines.base import EngineDegradedException, TileLocalFallback

TENANT = "test-tenant-tile-fallback"


@pytest.fixture
def selector():
    sel = EngineSelector()
    sel._engines[TENANT] = MagicMock()
    return sel


class TestSelectorTileDegradation:
    @pytest.mark.asyncio
    async def test_degraded_tile_records_tenant_and_raises_typed_exception(self, selector):
        """(a) Primary tile failure -> tenant recorded in `_tenant_degraded`
        with the exception's TTL, and `TileLocalFallback` is raised."""
        selector._engines[TENANT].get_tile = AsyncMock(
            side_effect=EngineDegradedException("token expired", retry_after_seconds=3600)
        )

        before = time.time()
        with pytest.raises(TileLocalFallback):
            await selector.get_tile(
                tenant_id=TENANT, index_type="NDVI", z=12, x=2000, y=1500,
            )

        assert TENANT in selector._tenant_degraded
        engine_name, until = selector._tenant_degraded[TENANT]
        assert engine_name == "fallback"
        # retry_after_seconds=3600 (auth-failure TTL) applied verbatim.
        assert before + 3600 <= until <= time.time() + 3600

    @pytest.mark.asyncio
    async def test_second_call_short_circuits_without_rehitting_primary(self, selector):
        """(b) Once degraded, a subsequent tile call for the same tenant
        never touches the primary engine again, and still signals fallback
        via `TileLocalFallback`."""
        selector._engines[TENANT].get_tile = AsyncMock(
            side_effect=EngineDegradedException("token expired", retry_after_seconds=3600)
        )

        with pytest.raises(TileLocalFallback):
            await selector.get_tile(
                tenant_id=TENANT, index_type="NDVI", z=12, x=2000, y=1500,
            )

        with pytest.raises(TileLocalFallback):
            await selector.get_tile(
                tenant_id=TENANT, index_type="NDVI", z=12, x=2000, y=1500,
            )

        # Primary engine's get_tile called exactly once (first attempt,
        # before degradation was recorded); the second call resolved
        # straight to the fallback engine via `_get_engine_for`.
        assert selector._engines[TENANT].get_tile.call_count == 1

    @pytest.mark.asyncio
    async def test_unexpected_error_does_not_record_degradation_but_still_falls_back(self, selector):
        """Mirrors `compute_indices`: an exception that is NOT
        `EngineDegradedException` still triggers `TileLocalFallback` (there is
        no other tile path) but does NOT populate `_tenant_degraded` — same
        "this request only" semantics as the compute path's generic
        `except Exception` branch."""
        selector._engines[TENANT].get_tile = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(TileLocalFallback):
            await selector.get_tile(
                tenant_id=TENANT, index_type="NDVI", z=12, x=2000, y=1500,
            )

        assert TENANT not in selector._tenant_degraded

    @pytest.mark.asyncio
    async def test_success_path_unchanged(self, selector):
        """Success path is untouched: a working primary engine's tile bytes
        are returned directly, no exception, no degradation recorded."""
        selector._engines[TENANT].get_tile = AsyncMock(return_value=b"\x89PNGok")

        result = await selector.get_tile(
            tenant_id=TENANT, index_type="NDVI", z=12, x=2000, y=1500,
        )

        assert result == b"\x89PNGok"
        assert TENANT not in selector._tenant_degraded

    @pytest.mark.asyncio
    async def test_degraded_tenant_tile_request_logs_no_error(self, selector, caplog):
        """Follow-up fix: once a tenant is already degraded, `_get_engine_for`
        short-circuits to `LocalProcessingEngine`, whose `get_tile`
        unconditionally raises `NotImplementedError`. That is expected,
        guaranteed behavior on EVERY tile request for up to the 1h degraded
        TTL — not an unexpected error — so it must NOT be logged at ERROR
        (a live map viewer would otherwise flood error dashboards). The
        fallback outcome itself is unchanged: `TileLocalFallback` is still
        raised."""
        selector._tenant_degraded[TENANT] = ("fallback", time.time() + 3600)

        with caplog.at_level(logging.DEBUG, logger="app.engines.selector"):
            with pytest.raises(TileLocalFallback):
                await selector.get_tile(
                    tenant_id=TENANT, index_type="NDVI", z=12, x=2000, y=1500,
                )

        error_records = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and r.name == "app.engines.selector"
        ]
        assert error_records == []


# ---------------------------------------------------------------------------
# (c) api/tiles.py endpoint — TileLocalFallback caught, falls through to COG
# ---------------------------------------------------------------------------

# Prevent FastAPI lifespan from opening a real DB connection at import.
with patch("app.database.init_db"):
    from app.main import app

from app.api.tiles import get_sentinel_hub_tile, get_db_session  # noqa: E402
from app.middleware.auth import require_auth  # noqa: E402

# NOTE: `get_db_session` is imported from `app.api.tiles` (the module that
# actually declares `Depends(get_db_session)`), not from `app.database`
# directly. `tests/test_results_latest.py` permanently replaces
# `sys.modules["app.database"]` with a MagicMock stub (never restored) —
# if this file ran `from app.database import get_db_session` after that
# stub was installed, it would bind to the stub's auto-generated attribute
# instead of the real function, and `app.dependency_overrides[get_db_session]`
# would silently fail to match the route's actual dependency (real DB
# connection attempted -> psycopg2 OperationalError). Importing from
# `app.api.tiles` always resolves to whatever object the route was bound
# to at router-include time, regardless of later `sys.modules` mutations
# elsewhere in the suite. Mirrors `test_calculate_routing.py`, which
# imports `get_db_with_tenant` from `app.api.scenes` for the same reason.


def _client(db, selector):
    from fastapi.testclient import TestClient

    def _override_auth():
        return {"tenant_id": TENANT, "user_id": "u-1", "roles": ["User"]}

    def _override_db():
        yield db

    app.dependency_overrides[require_auth] = _override_auth
    app.dependency_overrides[get_db_session] = _override_db
    app.state.engine_selector = selector
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Restore `app.dependency_overrides`/`app.state.engine_selector` after
    every test in this module, including methods inside
    `TestTilesEndpointCatchesTypedException`.

    A bare module-level `def teardown_function()` (xunit-style) only fires
    for top-level test *functions* — pytest does NOT call it for test
    *methods* defined inside a class (that requires `teardown_method`).
    Since `_client()`'s overrides were only ever cleaned up by such a
    `teardown_function`, they leaked forward into every test that runs
    afterward in the same process and shares this `app` singleton (e.g.
    `test_usage_api.py`, whose `test_missing_auth_rejects` silently passed
    auth via the leaked `require_auth` override). An autouse fixture runs
    for functions and class methods alike.
    """
    _unset = object()
    prior_selector = getattr(app.state, "engine_selector", _unset)
    yield
    app.dependency_overrides.clear()
    if prior_selector is _unset:
        if hasattr(app.state, "engine_selector"):
            del app.state.engine_selector
    else:
        app.state.engine_selector = prior_selector


class TestTilesEndpointCatchesTypedException:
    def test_tile_local_fallback_reaches_cog_lookup(self):
        """When the selector raises `TileLocalFallback`, the endpoint must
        catch it (not 500) and proceed to the local-COG query (step 3). With
        no matching completed job in the DB, that query legitimately 404s —
        proving execution reached step 3 rather than dying on an unhandled
        exception from the selector call."""
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        selector = MagicMock()
        selector.get_tile = AsyncMock(side_effect=TileLocalFallback("degraded, use local COG"))

        with patch("app.services.tile_cache.get_cached_tile", return_value=None), \
             patch("app.services.tile_cache.put_cached_tile"):
            client = _client(db, selector)
            resp = client.get("/api/vegetation/tiles/sentinel-hub/NDVI/12/2000/1500.png")

        assert resp.status_code == 404
        assert "No raster available" in resp.text
        selector.get_tile.assert_awaited_once()

    def test_bare_notimplementederror_still_caught_defensively(self):
        """Belt-and-braces: a direct `NotImplementedError` (e.g. from a
        future non-selector-mediated engine call) is still swallowed and
        falls through to the COG lookup, same as before this fix."""
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        selector = MagicMock()
        selector.get_tile = AsyncMock(side_effect=NotImplementedError("no tile support"))

        with patch("app.services.tile_cache.get_cached_tile", return_value=None), \
             patch("app.services.tile_cache.put_cached_tile"):
            client = _client(db, selector)
            resp = client.get("/api/vegetation/tiles/sentinel-hub/NDVI/12/2000/1500.png")

        assert resp.status_code == 404
        assert "No raster available" in resp.text
        selector.get_tile.assert_awaited_once()
