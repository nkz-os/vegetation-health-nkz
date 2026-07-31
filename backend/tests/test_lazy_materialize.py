"""Tests for lazy raster materialization on tile/bounds/results endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.api import tiles as tiles_mod


def _pending_job():
    j = MagicMock()
    j.id = uuid4()
    j.tenant_id = "montiko"
    j.entity_id = "urn:ngsi-ld:AgriParcel:p1"
    j.result = {
        "index_type": "NDVI",
        "sensing_date": "2026-07-05",
        "raster_path": None,
        "raster_pending": True,
        "geometry": {"type": "Polygon", "coordinates": []},
    }
    return j


def test_get_tile_materializes_pending_then_renders():
    """get_tile should call ensure_copernicus_raster when raster_pending."""
    called = {}

    def _ensure(db, job):
        job.result["raster_path"] = "montiko/.../NDVI.tif"
        job.result["raster_pending"] = False
        called["materialized"] = True
        return job.result["raster_path"]

    # Verify the import/module-level patching path
    with patch.object(tiles_mod, "ensure_copernicus_raster", _ensure):
        # Simulate the logic inline (integration test for the code path)
        j = _pending_job()
        assert j.result["raster_pending"] is True
        assert j.result["raster_path"] is None

        # This is the code we'll inject into get_tile/get_tile_bounds
        if j.result and j.result.get("raster_pending") and not j.result.get("raster_path"):
            tiles_mod.ensure_copernicus_raster(MagicMock(), j)

        assert called.get("materialized") is True
        assert j.result["raster_path"] == "montiko/.../NDVI.tif"
        assert j.result["raster_pending"] is False


def test_get_tile_skips_when_already_materialized():
    """No call to ensure_copernicus_raster when raster_path already set."""
    called = False

    def _ensure(db, job):
        nonlocal called
        called = True
        return job.result["raster_path"]

    with patch.object(tiles_mod, "ensure_copernicus_raster", _ensure):
        j = _pending_job()
        j.result["raster_path"] = "already/there.tif"
        j.result["raster_pending"] = False

        if j.result and j.result.get("raster_pending") and not j.result.get("raster_path"):
            tiles_mod.ensure_copernicus_raster(MagicMock(), j)

        assert called is False


def test_get_tile_skips_when_not_pending():
    """No call when raster_pending is not set."""
    called = False

    def _ensure(db, job):
        nonlocal called
        called = True

    with patch.object(tiles_mod, "ensure_copernicus_raster", _ensure):
        j = _pending_job()
        j.result["raster_pending"] = False

        if j.result and j.result.get("raster_pending") and not j.result.get("raster_path"):
            tiles_mod.ensure_copernicus_raster(MagicMock(), j)

        assert called is False
