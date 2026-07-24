"""Tests for Task 7: credential resolution offloaded off the event loop.

`_resolve_credentials` opens a synchronous SQLAlchemy `SessionLocal()` and
runs a blocking BYOK query. It is called from two async entry points in
`EngineSelector`:

  - `_get_engine_for` (via `compute_indices` / `get_tile`)
  - `is_sentinel_hub_usable` (sync method; its caller in `api/scenes.py`
    must offload the whole call, since a sync method cannot itself await)

These tests assert the DB work never runs directly on the loop: for
`_get_engine_for`, `asyncio.to_thread` must be invoked with
`_resolve_credentials` as the callable, and the resolved credentials must
still flow through into the per-tenant SentinelHubEngine correctly.
"""

import asyncio
import inspect
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.selector import EngineSelector


TENANT = "test-tenant-creds"


class TestGetEngineForOffloadsToThread:
    @pytest.mark.asyncio
    async def test_resolve_credentials_offloaded_via_to_thread(self):
        """`_get_engine_for` must call `asyncio.to_thread(_resolve_credentials,
        tenant_id)` rather than calling `_resolve_credentials` directly on
        the loop."""
        sel = EngineSelector()

        with patch(
            "app.engines.selector.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_to_thread:
            mock_to_thread.return_value = ("cid-123", "secret-456")

            engine = await sel._get_engine_for(TENANT)

        mock_to_thread.assert_awaited_once()
        args = mock_to_thread.call_args.args
        assert args[0].__name__ == "_resolve_credentials"
        assert args[1] == TENANT

        # Resolved credentials still flow through into the engine.
        assert engine._client._client_id == "cid-123"
        assert engine._client._client_secret == "secret-456"
        assert sel._engines[TENANT] is engine

    @pytest.mark.asyncio
    async def test_resolve_credentials_never_called_directly_on_loop(self):
        """Guard against a regression that calls `_resolve_credentials`
        synchronously inside `_get_engine_for` (defeats the offload)."""
        sel = EngineSelector()

        with patch(
            "app.engines.selector._resolve_credentials"
        ) as mock_resolve, patch(
            "app.engines.selector.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_to_thread:
            mock_to_thread.return_value = (None, None)

            await sel._get_engine_for(TENANT)

        # _resolve_credentials itself must be untouched by _get_engine_for —
        # it should only ever be reached indirectly, as the callable handed
        # to asyncio.to_thread.
        mock_resolve.assert_not_called()
        mock_to_thread.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_credentials_flow_through_real_thread_offload(self):
        """End-to-end (no to_thread patch): `_resolve_credentials` really
        runs in a worker thread and its result reaches the engine."""
        sel = EngineSelector()

        with patch(
            "app.engines.selector._resolve_credentials",
            return_value=("real-cid", "real-secret"),
        ) as mock_resolve:
            engine = await sel._get_engine_for(TENANT)

        mock_resolve.assert_called_once_with(TENANT)
        assert engine._client._client_id == "real-cid"
        assert engine._client._client_secret == "real-secret"

    def test_get_engine_for_is_a_coroutine_function(self):
        """Locks in the async contract so callers must `await` it."""
        assert inspect.iscoroutinefunction(EngineSelector._get_engine_for)


class TestComputeIndicesAndGetTileAwaitEngineResolution:
    @pytest.mark.asyncio
    async def test_compute_indices_awaits_get_engine_for(self):
        """`compute_indices` must await `_get_engine_for` (not call it
        synchronously, which would hand back an un-awaited coroutine)."""
        sel = EngineSelector()
        sel._engines[TENANT] = MagicMock()
        sel._engines[TENANT].compute_indices = AsyncMock(return_value=[])

        results = await sel.compute_indices(
            tenant_id=TENANT,
            parcel_id="p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_get_tile_awaits_get_engine_for(self):
        sel = EngineSelector()
        sel._engines[TENANT] = MagicMock()
        sel._engines[TENANT].get_tile = AsyncMock(return_value=b"tile-bytes")

        result = await sel.get_tile(
            tenant_id=TENANT, index_type="NDVI", z=10, x=1, y=1,
        )

        assert result == b"tile-bytes"


class TestIsSentinelHubUsableStaysSyncForCallerOffload:
    def test_is_sentinel_hub_usable_is_not_a_coroutine_function(self):
        """`is_sentinel_hub_usable` stays a plain sync method — its async
        caller in `api/scenes.py` is responsible for wrapping the whole call
        in `asyncio.to_thread` (a sync method cannot await internally)."""
        assert not inspect.iscoroutinefunction(EngineSelector.is_sentinel_hub_usable)

    def test_is_sentinel_hub_usable_resolves_credentials_synchronously(self):
        """Sanity: it still calls `_resolve_credentials` (unchanged
        contract) so `asyncio.to_thread(selector.is_sentinel_hub_usable, ...)`
        at the call site offloads the real blocking DB work."""
        sel = EngineSelector()

        with patch(
            "app.engines.selector._resolve_credentials",
            return_value=("cid", "secret"),
        ) as mock_resolve:
            usable = sel.is_sentinel_hub_usable(TENANT)

        mock_resolve.assert_called_once_with(TENANT)
        assert usable is True

    def test_is_sentinel_hub_usable_false_without_credentials(self):
        sel = EngineSelector()

        with patch(
            "app.engines.selector._resolve_credentials",
            return_value=(None, None),
        ):
            usable = sel.is_sentinel_hub_usable(TENANT)

        assert usable is False


class TestConcurrentColdStartDoesNotDuplicateResolution:
    """Task 7 follow-up: two concurrent first-time `_get_engine_for` calls for
    the SAME new tenant must not both pass the `not in self._engines` check,
    both construct a SentinelHubEngine, and both hit the BYOK DB — the
    `await asyncio.to_thread(...)` between the check and the cache write is
    an interleave point on the single-threaded event loop.

    A lock guarding the check-and-insert (with a double-check inside, mirroring
    `SentinelHubClient.get_token`'s token-refresh lock) must collapse this to
    exactly one resolution and one cached engine.
    """

    @pytest.mark.asyncio
    async def test_concurrent_cold_start_resolves_credentials_once(self):
        sel = EngineSelector()
        call_count = 0

        async def fake_to_thread(func, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Yield control back to the loop so a concurrent caller can
            # interleave here if the check-and-insert isn't locked.
            await asyncio.sleep(0.01)
            return ("cid-once", "secret-once")

        with patch(
            "app.engines.selector.asyncio.to_thread",
            side_effect=fake_to_thread,
        ):
            engine_a, engine_b = await asyncio.gather(
                sel._get_engine_for(TENANT),
                sel._get_engine_for(TENANT),
            )

        assert call_count == 1, (
            f"_resolve_credentials was offloaded {call_count} times for a "
            "single cold-start tenant — expected exactly 1"
        )
        assert engine_a is engine_b
        assert sel._engines[TENANT] is engine_a
