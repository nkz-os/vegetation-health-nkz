"""Tests for lazy raster materialization on the tile/bounds endpoints.

These drive the REAL `get_tile_bounds` async handler (not a re-implementation)
so they actually verify the handler routes pending Copernicus jobs through
`materialize_if_pending` (the off-event-loop offload) before reading the COG.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.api import tiles as tiles_mod
from app.api.tiles import get_tile_bounds


def _job(raster_path=None, pending=True):
    j = MagicMock()
    j.id = uuid4()
    j.tenant_id = "montiko"
    j.entity_id = "urn:ngsi-ld:AgriParcel:p1"
    j.result = {
        "index_type": "NDVI",
        "sensing_date": "2026-07-05",
        "raster_path": raster_path,
        "raster_pending": pending,
        "geometry": {"type": "Polygon", "coordinates": []},
    }
    return j


def _db_returning(job):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = job
    return db


@pytest.mark.asyncio
async def test_bounds_materializes_pending_via_offload_then_reads():
    job = _job(raster_path=None, pending=True)
    db = _db_returning(job)

    async def _materialize(db_, j):
        j.result["raster_path"] = "montiko/entities/p1/copernicus/2026-07-05/NDVI.tif"
        j.result["raster_pending"] = False
        return j.result["raster_path"]

    with patch.object(tiles_mod, "materialize_if_pending", side_effect=_materialize) as mat, \
         patch.object(tiles_mod, "_get_wgs84_bounds", return_value={"bounds": [-2.08, 42.63, -2.07, 42.64]}) as gb:
        out = await get_tile_bounds(str(job.id), db=db)

    mat.assert_awaited_once()                       # went through the offload wrapper
    gb.assert_called_once()
    assert gb.call_args.args[0].endswith("NDVI.tif")  # read the freshly-materialized COG
    assert out["bounds"] == [-2.08, 42.63, -2.07, 42.64]


@pytest.mark.asyncio
async def test_bounds_skips_materialize_when_already_present():
    job = _job(raster_path="already/there.tif", pending=False)
    db = _db_returning(job)

    with patch.object(tiles_mod, "materialize_if_pending", new_callable=AsyncMock) as mat, \
         patch.object(tiles_mod, "_get_wgs84_bounds", return_value={"bounds": [0, 0, 1, 1]}):
        out = await get_tile_bounds(str(job.id), db=db)

    mat.assert_not_awaited()
    assert out["bounds"] == [0, 0, 1, 1]


@pytest.mark.asyncio
async def test_bounds_404_when_no_raster_and_not_pending():
    from fastapi import HTTPException
    job = _job(raster_path=None, pending=False)
    db = _db_returning(job)

    with patch.object(tiles_mod, "materialize_if_pending", new_callable=AsyncMock) as mat:
        with pytest.raises(HTTPException) as ei:
            await get_tile_bounds(str(job.id), db=db)

    mat.assert_not_awaited()
    assert ei.value.status_code == 404
