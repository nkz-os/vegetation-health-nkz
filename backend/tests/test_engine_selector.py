"""Tests for EngineSelector — engine selection and auto-degradation."""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import date
from app.engines.selector import EngineSelector
from app.engines.base import (
    IndexResult, EngineHealth, EngineDegradedException,
)


@pytest.fixture
def selector():
    return EngineSelector()


class TestEngineSelection:
    @pytest.mark.asyncio
    async def test_default_to_primary(self, selector):
        """Without any failure, primary engine is used."""
        mock_result = [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.5, std=0.1, min=0.0, max=1.0,
                p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
                data_fidelity="sentinel_hub",
            )
        ]

        with patch.object(selector._primary, "compute_indices",
                          new_callable=AsyncMock) as mock_primary:
            mock_primary.return_value = mock_result

            results = await selector.compute_indices(
                tenant_id="t1",
                parcel_id="p1",
                parcel_geometry={"type": "Polygon", "coordinates": []},
                date_range=(date(2026, 7, 1), date(2026, 7, 31)),
                index_types=["NDVI"],
            )

        assert len(results) == 1
        assert results[0].data_fidelity == "sentinel_hub"
        mock_primary.assert_called_once()

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

        with patch.object(selector._primary, "compute_indices",
                          new_callable=AsyncMock) as mock_primary, \
             patch.object(selector._fallback, "compute_indices",
                          new_callable=AsyncMock) as mock_fallback:

            mock_primary.side_effect = EngineDegradedException("auth failed")
            mock_fallback.return_value = fallback_result

            results = await selector.compute_indices(
                tenant_id="t1",
                parcel_id="p1",
                parcel_geometry={"type": "Polygon", "coordinates": []},
                date_range=(date(2026, 7, 1), date(2026, 7, 31)),
                index_types=["NDVI"],
            )

        mock_fallback.assert_called_once()
        assert results[0].data_fidelity == "degraded_fallback"

    @pytest.mark.asyncio
    async def test_tenant_cached_as_fallback(self, selector):
        """After degradation, subsequent calls use fallback directly."""
        fallback_result = [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.5, std=0.1, min=0.0, max=1.0,
                p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
                data_fidelity="local_full",
            )
        ]

        with patch.object(selector._primary, "compute_indices",
                          new_callable=AsyncMock) as mock_primary, \
             patch.object(selector._fallback, "compute_indices",
                          new_callable=AsyncMock) as mock_fallback:

            mock_primary.side_effect = EngineDegradedException("auth failed")
            mock_fallback.return_value = fallback_result

            await selector.compute_indices(
                tenant_id="t1", parcel_id="p1",
                parcel_geometry={"type": "Polygon", "coordinates": []},
                date_range=(date(2026, 7, 1), date(2026, 7, 31)),
                index_types=["NDVI"],
            )

            await selector.compute_indices(
                tenant_id="t1", parcel_id="p1",
                parcel_geometry={"type": "Polygon", "coordinates": []},
                date_range=(date(2026, 7, 1), date(2026, 7, 31)),
                index_types=["NDVI"],
            )

        assert mock_primary.call_count == 1
        assert mock_fallback.call_count == 2

    @pytest.mark.asyncio
    async def test_tile_degradation(self, selector):
        """Tile requests also degrade on failure."""
        fallback_tile = b"\x89PNGfallback"

        with patch.object(selector._primary, "get_tile",
                          new_callable=AsyncMock) as mock_primary, \
             patch.object(selector._fallback, "get_tile",
                          new_callable=AsyncMock) as mock_fallback:

            mock_primary.side_effect = EngineDegradedException("token expired")
            mock_fallback.return_value = fallback_tile

            result = await selector.get_tile(
                tenant_id="t1", index_type="NDVI", z=12, x=2000, y=1500,
            )

        mock_fallback.assert_called_once()
        assert result == fallback_tile
