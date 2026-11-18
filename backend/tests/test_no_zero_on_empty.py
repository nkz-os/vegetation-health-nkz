"""An index with no valid pixels in the parcel must never be published."""
import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "shapely", "shapely.geometry",
    "shapely.ops", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import pytest

import app.services.fiware_integration as fi
import app.tasks.processing_tasks as pt
from tests.fake_orion import FakeAsyncOrion

PARCEL = "urn:ngsi-ld:AgriParcel:parcel-1"
DAY = date(2026, 6, 21)


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAsyncOrion.last_instance = None
    yield
    FakeAsyncOrion.last_instance = None


def _upsert(stats):
    with patch.object(fi, "OrionClient", FakeAsyncOrion):
        return fi.upsert_eo_index("acme", PARCEL, "NDVI", stats, DAY)


def test_upsert_eo_index_skips_zero_pixel_stats():
    stats = {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0, "pixel_count": 0}
    assert _upsert(stats) is None
    fake = FakeAsyncOrion.last_instance
    assert fake is None or not fake.calls


def test_upsert_eo_index_skips_missing_pixel_count():
    assert _upsert({"mean": 0.5}) is None
    fake = FakeAsyncOrion.last_instance
    assert fake is None or not fake.calls


def test_upsert_eo_index_skips_none_pixel_count():
    assert _upsert({"mean": 0.5, "pixel_count": None}) is None
    fake = FakeAsyncOrion.last_instance
    assert fake is None or not fake.calls


def test_upsert_eo_index_publishes_with_pixels():
    stats = {"mean": 0.61, "min": 0.1, "max": 0.9, "std": 0.05, "pixel_count": 42}
    assert _upsert(stats) is not None
    body = FakeAsyncOrion.last_instance.entities[0]
    assert body["ndvi"]["value"] == 0.61
    assert body["pixelCount"]["value"] == 42


def _persist(stats):
    job = MagicMock()
    job.id = "job-1"
    job.entity_id = PARCEL
    scene = MagicMock()
    scene.sensing_date = DAY
    scene.cloud_coverage = 3.0
    return pt._persist_results(
        tenant_id="acme", job=job, index_type="NDVI", formula=None,
        statistics=stats, remote_raster_path="acme/x.tif", primary_scene=scene,
    )


def test_persist_results_skips_without_calling_upsert():
    with patch.object(pt, "upsert_eo_index", MagicMock()) as up:
        assert _persist({"mean": 0.0, "pixel_count": 0}) is False
        up.assert_not_called()


def test_persist_results_returns_true_when_published():
    with patch.object(pt, "upsert_eo_index", MagicMock(return_value="urn:ngsi-ld:EOProduct:x")) as up:
        assert _persist({"mean": 0.6, "pixel_count": 5}) is True
        up.assert_called_once()
