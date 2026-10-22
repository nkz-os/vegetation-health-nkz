"""Tests for EngineSelector — per-tenant engine isolation and auto-degradation."""

import time

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date
from app.engines.selector import EngineSelector
from app.engines.base import (
    IndexResult, EngineHealth, EngineDegradedException, TileLocalFallback,
)


@pytest.fixture
def selector():
    sel = EngineSelector()
    # Pre-seed a mocked primary engine for tenant "t1" so tests don't
    # create real SentinelHubClient instances.
    sel._engines["t1"] = MagicMock()
    return sel


class TestEngineSelection:
    @pytest.mark.asyncio
    async def test_default_to_primary(self, selector):
        """Without any failure, the per-tenant primary engine is used."""
        mock_result = [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.5, std=0.1, min=0.0, max=1.0,
                p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
                data_fidelity="sentinel_hub",
            )
        ]

        selector._engines["t1"].compute_indices = AsyncMock(return_value=mock_result)

        results = await selector.compute_indices(
            tenant_id="t1",
            parcel_id="p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )

        assert len(results) == 1
        assert results[0].data_fidelity == "sentinel_hub"
        selector._engines["t1"].compute_indices.assert_called_once()

    @pytest.mark.asyncio
    async def test_degrade_on_auth_failure(self, selector):
        """Primary raises EngineDegradedException → fallback used."""
        fallback_result = [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.5, std=0.1, min=0.0, max=1.0,
                p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
                data_fidelity="local_full",
            )
        ]

        selector._engines["t1"].compute_indices = AsyncMock(
            side_effect=EngineDegradedException("auth failed")
        )
        selector._fallback.compute_indices = AsyncMock(return_value=fallback_result)

        results = await selector.compute_indices(
            tenant_id="t1",
            parcel_id="p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )

        selector._fallback.compute_indices.assert_called_once()
        assert results[0].data_fidelity == "degraded_fallback"

    @pytest.mark.asyncio
    async def test_tenant_cached_as_fallback(self, selector):
        """After degradation, subsequent calls skip primary and use fallback."""
        fallback_result = [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.5, std=0.1, min=0.0, max=1.0,
                p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
                data_fidelity="local_full",
            )
        ]

        selector._engines["t1"].compute_indices = AsyncMock(
            side_effect=EngineDegradedException("auth failed")
        )
        selector._fallback.compute_indices = AsyncMock(return_value=fallback_result)

        # First call: primary fails → degrades
        await selector.compute_indices(
            tenant_id="t1", parcel_id="p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )

        # Second call: tenant already degraded → uses fallback directly
        await selector.compute_indices(
            tenant_id="t1", parcel_id="p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )

        # Primary only called once (first attempt before caching degradation)
        assert selector._engines["t1"].compute_indices.call_count == 1
        assert selector._fallback.compute_indices.call_count == 2

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, selector):
        """Tenant A degrading does NOT affect tenant B."""
        # Seed a separate engine for tenant "t2"
        selector._engines["t2"] = MagicMock()

        result_a = [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.5, std=0.1, min=0.0, max=1.0,
                p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
                data_fidelity="sentinel_hub",
            )
        ]
        fallback_result = [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.5, std=0.1, min=0.0, max=1.0,
                p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
                data_fidelity="local_full",
            )
        ]

        # Tenant A degrades
        selector._engines["t1"].compute_indices = AsyncMock(
            side_effect=EngineDegradedException("auth failed")
        )
        # Tenant B is fine
        selector._engines["t2"].compute_indices = AsyncMock(return_value=result_a)
        selector._fallback.compute_indices = AsyncMock(return_value=fallback_result)

        # Tenant A request → degrades
        r1 = await selector.compute_indices(
            tenant_id="t1", parcel_id="p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )
        assert r1[0].data_fidelity == "degraded_fallback"

        # Tenant B request → should use its own primary engine
        r2 = await selector.compute_indices(
            tenant_id="t2", parcel_id="p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )
        assert r2[0].data_fidelity == "sentinel_hub"
        selector._engines["t2"].compute_indices.assert_called_once()

    @pytest.mark.asyncio
    async def test_tile_degradation(self, selector):
        """Tile requests degrade (recorded in `_tenant_degraded`) and raise
        `TileLocalFallback` instead of calling `self._fallback.get_tile`
        directly — `LocalProcessingEngine.get_tile` unconditionally raises
        `NotImplementedError` (Phase 3 pending), so the real local-tile
        fallback lives in `api/tiles.py`'s COG query, not the selector."""
        selector._engines["t1"].get_tile = AsyncMock(
            side_effect=EngineDegradedException("token expired", retry_after_seconds=3600)
        )

        with pytest.raises(TileLocalFallback):
            await selector.get_tile(
                tenant_id="t1", index_type="NDVI", z=12, x=2000, y=1500,
            )

        assert "t1" in selector._tenant_degraded
        engine_name, until = selector._tenant_degraded["t1"]
        assert engine_name == "fallback"
        assert until > time.time()
