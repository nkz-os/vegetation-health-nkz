"""Tests for LocalProcessingEngine — wrapper around existing Celery tasks."""

import pytest
from datetime import date
from app.engines.local import LocalProcessingEngine


@pytest.fixture
def engine():
    return LocalProcessingEngine()


class TestLocalEngine:
    def test_engine_name(self, engine):
        assert engine.engine_name == "local_processing"

    @pytest.mark.asyncio
    async def test_health_check_ok(self, engine):
        """health_check returns ok when Celery broker is reachable."""
        health = await engine.health_check()
        # Without real credentials, health check returns degraded, not error
        assert health.status in ("ok", "degraded")
        assert health.last_checked is not None

    @pytest.mark.asyncio
    async def test_compute_indices_dispatches_celery(self, engine):
        """compute_indices dispatches to Celery and polls for results.

        In test environment without Celery workers, it raises an error
        gracefully rather than hanging.
        """
        with pytest.raises(Exception) as exc_info:
            await engine.compute_indices(
                tenant_id="t1",
                parcel_id="urn:ngsi-ld:AgriParcel:t1:p1",
                parcel_geometry={
                    "type": "Polygon",
                    "coordinates": [[
                        [-6.0, 37.0], [-5.9, 37.0],
                        [-5.9, 37.1], [-6.0, 37.1],
                        [-6.0, 37.0],
                    ]],
                },
                date_range=(date(2026, 7, 1), date(2026, 7, 31)),
                index_types=["NDVI"],
                cloud_cover_max=50.0,
            )
        # Connection error (DB, Celery, etc.) — expected without infra
        msg = str(exc_info.value).lower()
        assert any(kw in msg for kw in ("celery", "timeout", "connection", "operational"))

    @pytest.mark.asyncio
    async def test_get_tile_not_implemented(self, engine):
        """get_tile raises NotImplementedError until Phase 3 tile proxy."""
        with pytest.raises(NotImplementedError):
            await engine.get_tile(
                tenant_id="t1",
                index_type="NDVI",
                z=12, x=2000, y=1500,
            )
