"""Tests for copernicus_raster.py — ensure_copernicus_raster, _dynamic_res."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.copernicus_raster import (
    ensure_copernicus_raster,
    materialize_if_pending,
    _dynamic_res,
)


def _job(raster_path=None, pending=True):
    j = MagicMock()
    j.id = "job-1"
    j.tenant_id = "montiko"
    j.entity_id = "urn:ngsi-ld:AgriParcel:p1"
    j.result = {
        "index_type": "NDVI",
        "sensing_date": "2026-07-20",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-2.08, 42.63], [-2.07, 42.63], [-2.07, 42.64], [-2.08, 42.64], [-2.08, 42.63]]],
        },
        "raster_path": raster_path,
        "raster_pending": pending,
    }
    return j


def test_dynamic_res_small_parcel_native_10m():
    resx, resy = _dynamic_res([-2.08, 42.63, -2.07, 42.64])
    assert resx == pytest.approx(0.0001) and resy == pytest.approx(0.0001)


def test_dynamic_res_large_parcel_coarsened():
    # ~1 degree span → >2048 px at 0.0001 → coarsened
    resx, resy = _dynamic_res([-1.0, 42.0, 0.0, 43.0])
    assert resx > 0.0001 and resy > 0.0001


def test_ensure_idempotent_when_raster_present():
    db = MagicMock()
    j = _job(raster_path="montiko/entities/p1/copernicus/2026-07-20/NDVI.tif", pending=False)
    assert ensure_copernicus_raster(db, j) == j.result["raster_path"]


def test_ensure_generates_cog_and_sets_path():
    db = MagicMock()
    j = _job()
    with patch("app.services.copernicus_raster._resolve_credentials", return_value=("id", "sec")), \
         patch("app.services.copernicus_raster._process_and_cog", return_value=b"COGBYTES") as pc, \
         patch("app.services.copernicus_raster._upload") as up, \
         patch("app.services.copernicus_raster.generate_tenant_bucket_name", return_value="veg-montiko"):
        path = ensure_copernicus_raster(db, j)
    assert path and path.endswith("NDVI.tif")
    assert j.result["raster_path"] == path
    assert j.result.get("raster_pending") in (False, None)
    up.assert_called_once()
    db.commit.assert_called()


def test_ensure_returns_none_on_sh_failure_keeps_pending():
    db = MagicMock()
    j = _job()
    with patch("app.services.copernicus_raster._resolve_credentials", return_value=("id", "sec")), \
         patch("app.services.copernicus_raster._process_and_cog", side_effect=RuntimeError("SH 500")):
        assert ensure_copernicus_raster(db, j) is None
    assert j.result["raster_pending"] is True
    assert j.result["raster_path"] is None


@pytest.mark.asyncio
async def test_materialize_if_pending_runs_off_event_loop():
    """Regression (the #23 production bug): ensure_copernicus_raster is sync and
    internally calls asyncio.run() for the SH round-trip. It MUST be offloaded via
    materialize_if_pending -> asyncio.to_thread so it does not raise
    'asyncio.run() cannot be called from a running event loop' (which the broker
    swallowed as a silent 'materialization failure', leaving raster_pending forever).

    This drives the REAL ensure_copernicus_raster from inside pytest-asyncio's
    running loop, mocking only the network boundary + the MinIO upload. If someone
    reverts the to_thread offload, ensure runs on the loop thread, asyncio.run
    raises, the except swallows it -> raster_path stays None -> this test FAILS.
    """
    db = MagicMock()
    db.refresh.side_effect = lambda o: None
    job = _job(raster_path=None, pending=True)

    with patch("app.services.copernicus_raster._resolve_credentials",
               return_value=("id", "sec")), \
         patch("app.services.sentinel_hub_client.SentinelHubClient.process_raster",
               new_callable=AsyncMock, return_value=b"II*\x00fake-tiff"), \
         patch("app.services.copernicus_raster._upload") as up:
        # cog_translate on the fake bytes fails internally -> falls back to raw bytes.
        path = await materialize_if_pending(db, job)

    assert path is not None and path.endswith("NDVI.tif")
    assert job.result["raster_path"] == path
    assert job.result["raster_pending"] is False
    up.assert_called_once()


@pytest.mark.asyncio
async def test_materialize_if_pending_noop_when_not_pending():
    """Already-materialized job: return the path, never touch the network/thread."""
    db = MagicMock()
    job = _job(raster_path="montiko/entities/p1/copernicus/2026-07-20/NDVI.tif", pending=False)
    with patch("app.services.copernicus_raster.ensure_copernicus_raster") as ensure:
        path = await materialize_if_pending(db, job)
    assert path == job.result["raster_path"]
    ensure.assert_not_called()
