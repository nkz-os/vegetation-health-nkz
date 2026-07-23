"""Verify the base engine interface can be subclassed correctly."""

import pytest
from datetime import date
from app.engines.base import (
    BaseVegetationEngine, IndexResult, EngineHealth,
    EngineDegradedException,
)


class DummyEngine(BaseVegetationEngine):
    """Minimal concrete engine for interface testing."""

    async def compute_indices(self, tenant_id, parcel_id, parcel_geometry,
                              date_range, index_types, cloud_cover_max=50.0):
        return [
            IndexResult(
                index_type="NDVI", sensing_date=date(2026, 7, 20),
                mean=0.65, std=0.12, min=0.1, max=0.92,
                p10=0.3, p90=0.85, valid_pixels=100, total_pixels=110,
                data_fidelity="sentinel_hub",
            )
        ]

    async def get_tile(self, tenant_id, index_type, z, x, y,
                       date_str=None, color_ramp="agronomic"):
        return b"\x89PNG\r\n\x1a\n"

    async def health_check(self):
        return EngineHealth(status="ok")

    @property
    def engine_name(self):
        return "dummy"


class TestBaseEngineInterface:
    def test_subclass_instantiation(self):
        engine = DummyEngine()
        assert engine.engine_name == "dummy"

    @pytest.mark.asyncio
    async def test_compute_indices_signature(self):
        engine = DummyEngine()
        results = await engine.compute_indices(
            tenant_id="t1",
            parcel_id="urn:ngsi-ld:AgriParcel:t1:p1",
            parcel_geometry={"type": "Polygon", "coordinates": []},
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            index_types=["NDVI"],
        )
        assert len(results) == 1
        assert results[0].index_type == "NDVI"
        assert results[0].data_fidelity == "sentinel_hub"

    @pytest.mark.asyncio
    async def test_get_tile(self):
        engine = DummyEngine()
        tile = await engine.get_tile("t1", "NDVI", 12, 2000, 1500)
        assert isinstance(tile, bytes)
        assert tile[:4] == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_health_check(self):
        engine = DummyEngine()
        health = await engine.health_check()
        assert health.status == "ok"
        assert health.last_checked is not None


class TestIndexResult:
    def test_default_data_fidelity(self):
        r = IndexResult(
            index_type="NDVI", sensing_date=date(2026, 7, 20),
            mean=0.5, std=0.1, min=0.0, max=1.0,
            p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
        )
        assert r.data_fidelity == "sentinel_hub"

    def test_custom_data_fidelity(self):
        r = IndexResult(
            index_type="NDVI", sensing_date=date(2026, 7, 20),
            mean=0.5, std=0.1, min=0.0, max=1.0,
            p10=0.2, p90=0.8, valid_pixels=50, total_pixels=50,
            data_fidelity="degraded_fallback",
        )
        assert r.data_fidelity == "degraded_fallback"


class TestEngineDegradedException:
    def test_default_retry(self):
        exc = EngineDegradedException("token expired")
        assert exc.reason == "token expired"
        assert exc.retry_after_seconds == 3600

    def test_custom_retry(self):
        exc = EngineDegradedException("rate limited", retry_after_seconds=300)
        assert exc.retry_after_seconds == 300
