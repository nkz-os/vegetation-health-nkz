"""Tests for SentinelHubEngine — compute_indices and get_tile."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date
from app.engines.sentinel_hub import SentinelHubEngine
from app.engines.base import IndexResult


@pytest.fixture
def engine():
    return SentinelHubEngine(
        client_id="test-id",
        client_secret="test-secret",
    )


@pytest.fixture
def parcel_geometry():
    return {
        "type": "Polygon",
        "coordinates": [[[-5.1, 40.1], [-5.0, 40.1], [-5.0, 40.2], [-5.1, 40.2], [-5.1, 40.1]]],
    }


class TestComputeIndices:
    @pytest.mark.asyncio
    async def test_compute_single_index(self, engine, parcel_geometry):
        """compute_indices parses Statistical API response into IndexResult list."""
        mock_response = {
            "data": [
                {
                    "interval": {"from": "2026-07-15T00:00:00Z", "to": "2026-07-20T00:00:00Z"},
                    "outputs": {
                        "ndvi": {"bands": {"B0": {"stats": {
                            "mean": 0.72, "stDev": 0.15, "min": 0.1, "max": 0.95,
                            "percentiles": {"10": 0.35, "90": 0.88},
                            "sampleCount": 100, "noDataCount": 10,
                        }}}},
                    },
                }
            ]
        }

        with patch.object(engine, "_client") as mock_client:
            mock_client.statistical = AsyncMock(return_value=mock_response)

            results = await engine.compute_indices(
                tenant_id="t1",
                parcel_id="urn:ngsi-ld:AgriParcel:t1:p1",
                parcel_geometry=parcel_geometry,
                date_range=(date(2026, 7, 15), date(2026, 7, 20)),
                index_types=["NDVI"],
            )

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, IndexResult)
        assert r.index_type == "NDVI"
        assert r.sensing_date == date(2026, 7, 17)  # midpoint of 5-day window
        assert r.mean == 0.72
        assert r.std == 0.15
        assert r.min == 0.1
        assert r.max == 0.95
        assert r.p10 == 0.35
        assert r.p90 == 0.88
        assert r.valid_pixels == 100
        assert r.total_pixels == 110
        assert r.data_fidelity == "sentinel_hub"

    @pytest.mark.asyncio
    async def test_compute_multi_index(self, engine, parcel_geometry):
        """All five indices parsed from a multi-index response."""
        mock_response = {
            "data": [
                {
                    "interval": {"from": "2026-07-15T00:00:00Z", "to": "2026-07-20T00:00:00Z"},
                    "outputs": {
                        "ndvi":  {"bands": {"B0": {"stats": {"mean": 0.72, "stDev": 0.1, "min": 0.0, "max": 1.0, "percentiles": {"10": 0.3, "90": 0.9}, "sampleCount": 50, "noDataCount": 0}}}},
                        "evi":   {"bands": {"B0": {"stats": {"mean": 0.45, "stDev": 0.1, "min": 0.0, "max": 0.8, "percentiles": {"10": 0.2, "90": 0.7}, "sampleCount": 50, "noDataCount": 0}}}},
                        "savi":  {"bands": {"B0": {"stats": {"mean": 0.55, "stDev": 0.1, "min": 0.0, "max": 0.9, "percentiles": {"10": 0.2, "90": 0.8}, "sampleCount": 50, "noDataCount": 0}}}},
                        "gndvi": {"bands": {"B0": {"stats": {"mean": 0.60, "stDev": 0.1, "min": 0.0, "max": 0.9, "percentiles": {"10": 0.3, "90": 0.8}, "sampleCount": 50, "noDataCount": 0}}}},
                        "ndre":  {"bands": {"B0": {"stats": {"mean": 0.35, "stDev": 0.1, "min": 0.0, "max": 0.6, "percentiles": {"10": 0.1, "90": 0.5}, "sampleCount": 50, "noDataCount": 0}}}},
                    },
                }
            ]
        }

        with patch.object(engine, "_client") as mock_client:
            mock_client.statistical = AsyncMock(return_value=mock_response)

            results = await engine.compute_indices(
                tenant_id="t1",
                parcel_id="urn:ngsi-ld:AgriParcel:t1:p1",
                parcel_geometry=parcel_geometry,
                date_range=(date(2026, 7, 15), date(2026, 7, 20)),
                index_types=["NDVI", "EVI", "SAVI", "GNDVI", "NDRE"],
            )

        assert len(results) == 5
        index_types = {r.index_type for r in results}
        assert index_types == {"NDVI", "EVI", "SAVI", "GNDVI", "NDRE"}

    @pytest.mark.asyncio
    async def test_health_check_ok(self, engine):
        """health_check returns ok when token refresh succeeds."""
        with patch.object(engine, "_client") as mock_client:
            mock_client.get_token = AsyncMock(return_value="fake-token")
            health = await engine.health_check()
            assert health.status == "ok"

    @pytest.mark.asyncio
    async def test_health_check_unavailable(self, engine):
        """health_check returns unavailable when auth fails."""
        from app.services.sentinel_hub_client import SentinelHubAuthError

        with patch.object(engine, "_client") as mock_client:
            mock_client.get_token = AsyncMock(
                side_effect=SentinelHubAuthError("bad credentials")
            )
            health = await engine.health_check()
            assert health.status == "unavailable"
            assert "bad credentials" in (health.reason or "")


class TestEngineName:
    def test_engine_name(self, engine):
        assert engine.engine_name == "sentinel_hub"
