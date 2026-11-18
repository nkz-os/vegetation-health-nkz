"""One observation per date: the scene with the best valid interior fraction wins."""
import os
import sys
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "shapely", "shapely.geometry",
    "shapely.ops", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import numpy as np
import pytest

import app.tasks.processing_tasks as pt
from app.models import VegetationJob, VegetationScene
from app.services import parcel_scenes

TENANT = "tenant-a"
PARCEL = "urn:ngsi-ld:AgriParcel:parcel-1"
DAY = date(2026, 6, 21)
URL_A = "s3://bucket/tenant-a/entities/parcel-1/scenes/11111111-1111-1111-1111-111111111111/NDVI.tif"
URL_B = "s3://bucket/tenant-a/entities/parcel-1/scenes/22222222-2222-2222-2222-222222222222/NDVI.tif"


def test_is_better_observation_rules():
    better = pt.is_better_observation
    assert better(0.5, URL_A, None) is True
    assert better(0.5, URL_A, (None, URL_B)) is True        # product from before the interior measure
    assert better(0.5, URL_A, (0.9, URL_A)) is True         # same scene recomputed
    assert better(0.5, URL_A, (0.8, URL_B)) is False
    assert better(0.9, URL_A, (0.8, URL_B)) is True
    assert better(0.8, URL_A, (0.8, URL_B)) is False        # tie keeps the existing one
    assert better(0.5, URL_A, (0.9, None)) is False


def test_idempotency_key_includes_scene():
    k1 = pt._idempotency_key(TENANT, PARCEL, "NDVI", "2026-06-21", "S2A_ONE")
    k2 = pt._idempotency_key(TENANT, PARCEL, "NDVI", "2026-06-21", "S2B_TWO")
    assert k1 != k2 and k1.endswith(":2026-06-21:S2A_ONE")
    redis = MagicMock()
    redis.set.return_value = True
    with patch.object(pt, "_get_redis", return_value=redis):
        assert pt._check_idempotency(TENANT, PARCEL, "NDVI", "2026-06-21", "S2A_ONE") is False
        pt._release_idempotency(TENANT, PARCEL, "NDVI", "2026-06-21", "S2A_ONE")
    assert redis.set.call_args.args[0] == k1
    redis.delete.assert_called_once_with(k1)


def test_release_scene_idempotency_uses_the_scene_key():
    released = []
    with patch.object(pt, "_release_idempotency", lambda *a: released.append(a)):
        pt.release_scene_idempotency(TENANT, PARCEL, "2026-06-21", "S2A_ONE")
    assert released and all(a[3] == "2026-06-21" and a[4] == "S2A_ONE" for a in released)


# --- calculate path -------------------------------------------------------

class _FakeProcessor:
    """Stands in for VegetationIndexProcessor: no raster I/O."""
    valid_fraction = 0.5

    def __init__(self, band_paths=None, bbox=None, radiometry=None):
        self.band_paths = band_paths or {}
        self.band_data = {"SCL": np.zeros((4, 4))}
        self.band_meta = {"crs": "EPSG:32630", "height": 4, "width": 4, "transform": None}

    def load_bands(self, bands):
        pass

    def calculate_ndvi(self):
        arr = np.full((4, 4), 0.6, dtype=np.float32)
        arr.flat[: int(round(16 * (1 - self.valid_fraction)))] = np.nan
        return arr

    def create_geometry_mask(self, geojson):
        return np.zeros((4, 4), dtype=bool)

    def calculate_statistics(self, arr, mask=None):
        v = arr[np.isfinite(arr)]
        return {"mean": float(v.mean()), "min": float(v.min()), "max": float(v.max()),
                "std": float(v.std()), "pixel_count": int(v.size)}

    def vectorize_index(self, *a, **k):
        return None

    def save_index_raster(self, arr, path):
        return path


def _q(value):
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.first.return_value = value
    q.all.return_value = [value] if value is not None else []
    return q


def _run(existing=None, existing_exc=None, valid_fraction=0.5):
    job = MagicMock()
    job.id = uuid.uuid4()
    job.entity_id = PARCEL
    job.parameters = {"index_type": "NDVI",
                      "bounds": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
    job.start_date = job.end_date = None
    scene = MagicMock()
    scene.id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    scene.scene_id = "S2B_TWO"
    scene.sensing_date = DAY
    scene.cloud_coverage = 2.0
    scene.storage_bucket = "bucket"
    scene.bands = {"B04": "b/B04.tif", "B08": "b/B08.tif", "SCL": "b/SCL.tif"}
    db = MagicMock()
    db.query.side_effect = lambda model: _q(
        job if model is VegetationJob else scene if model is VegetationScene else None)
    proc = type("P", (_FakeProcessor,), {"valid_fraction": valid_fraction})
    reader = MagicMock(side_effect=existing_exc) if existing_exc else MagicMock(return_value=existing)
    interior_mask = MagicMock(return_value=(np.ones((4, 4), dtype=bool), "core"))
    parcel_mask = MagicMock(return_value=np.ones((4, 4), dtype=bool))
    with patch.object(pt, "get_db_session", return_value=iter([db])), \
         patch.object(pt, "_check_idempotency", return_value=False), \
         patch.object(pt, "_decrement_and_cleanup_bands"), \
         patch.object(pt, "create_storage_service", return_value=MagicMock()), \
         patch.object(pt, "VegetationIndexProcessor", proc), \
         patch.object(pt, "l2a_radiometry", return_value=None), \
         patch.object(pt, "interior_mask", interior_mask), \
         patch.object(pt, "parcel_mask", parcel_mask), \
         patch.object(pt, "existing_interior_observation", reader), \
         patch.object(pt, "upsert_eo_index", return_value="urn:ngsi-ld:EOProduct:x") as upsert, \
         patch.object(pt.parcel_scenes, "record") as record, \
         patch("app.services.window_crop.crop_bands_to_window", lambda paths, *a, **k: paths), \
         patch("app.services.zone_products.publish_zone_indices") as zones, \
         patch.object(pt.calculate_vegetation_index, "update_state"):
        pt.calculate_vegetation_index.run(str(job.id), TENANT, str(scene.id), "NDVI", None, None, None)
    return job, upsert, zones, record, reader


def test_worse_same_day_scene_not_published():
    job, upsert, zones, record, reader = _run(existing=(0.9, URL_A), valid_fraction=0.5)
    reader.assert_called_once()
    upsert.assert_not_called()
    zones.assert_not_called()
    result = job.mark_completed.call_args.args[0]
    assert result["published"] is False and result["skip_reason"] == "better_scene_same_date"
    assert result["interior"]["valid_fraction"] == 0.5
    record.assert_called_once_with(TENANT, PARCEL, "S2B_TWO", parcel_scenes.DONE, sensing_date=DAY)


def test_better_same_day_scene_is_published_with_interior():
    job, upsert, zones, record, _ = _run(existing=(0.4, URL_A), valid_fraction=0.75)
    upsert.assert_called_once()
    kw = upsert.call_args.kwargs
    assert kw["interior"]["valid_fraction"] == 0.75 and kw["interior"]["mask"] == "core"
    assert kw["parcel_cloud_fraction"] == 0.0
    zones.assert_called_once()
    assert job.mark_completed.call_args.args[0]["published"] is True


def test_reader_failure_fails_open():
    job, upsert, zones, record, _ = _run(existing_exc=RuntimeError("broker down"), valid_fraction=0.5)
    upsert.assert_called_once()
    zones.assert_called_once()
    assert job.mark_completed.call_args.args[0]["published"] is True
