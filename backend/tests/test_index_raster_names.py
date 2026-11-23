"""Index rasters are named after their analytics name; a shared raster is deleted once, with its last user."""
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "shapely", "shapely.geometry",
    "shapely.ops", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import app.tasks.processing_tasks as pt

TENANT = "tenant-a"
PARCEL = "urn:ngsi-ld:AgriParcel:parcel-1"
WINDOW = ("2026-06-01", "2026-06-21")


def test_standard_index_is_named_after_its_analytics_name_in_the_scene_folder():
    key = pt.index_raster_key(TENANT, PARCEL, "NDRE", job_id="j", scene_uuid="s")
    assert key == f"{TENANT}/entities/{PARCEL}/scenes/s/ndre.tif"


def test_composite_has_one_folder_per_window_shared_by_its_indices():
    ndvi = pt.index_raster_key(TENANT, PARCEL, "NDVI", job_id="j1", window=WINDOW)
    evi = pt.index_raster_key(TENANT, PARCEL, "EVI", job_id="j2", window=WINDOW)
    assert ndvi == f"{TENANT}/entities/{PARCEL}/composites/2026-06-01_2026-06-21/ndvi.tif"
    assert evi.rsplit("/", 1)[0] == ndvi.rsplit("/", 1)[0]
    other = pt.index_raster_key(TENANT, PARCEL, "NDVI", job_id="j3", window=("2026-06-10", WINDOW[1]))
    assert other.rsplit("/", 1)[0] != ndvi.rsplit("/", 1)[0]


def test_custom_index_keeps_its_name_and_its_per_job_composite_folder():
    assert pt.index_raster_key(TENANT, PARCEL, "CUSTOM", job_id="j", scene_uuid="s").endswith("/scenes/s/CUSTOM.tif")
    assert pt.index_raster_key(TENANT, PARCEL, "CUSTOM", job_id="j", window=WINDOW).endswith(
        "/composites/j/CUSTOM.tif")


def test_job_without_a_parcel_keeps_the_per_job_composite_folder():
    # One shared "unknown/composites/{window}" folder would join unrelated jobs.
    assert pt.index_raster_key(TENANT, None, "NDVI", job_id="j", window=WINDOW) == \
        f"{TENANT}/entities/unknown/composites/j/ndvi.tif"
    assert pt.index_raster_key(TENANT, None, "NDVI", job_id="j", scene_uuid="s") == \
        f"{TENANT}/entities/unknown/scenes/s/ndvi.tif"


def _hard_delete(others: int):
    from app.api import parcels

    job = MagicMock()
    job.status = "completed"
    job.job_type = "calculate_index"
    job.result = {"raster_path": f"{TENANT}/entities/{PARCEL}/composites/a_b/ndvi.tif"}
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = others
    with patch.object(parcels, "_delete_raster_safely") as drop:
        parcels._hard_delete_one(db, TENANT, job)
    return db, drop


def test_hard_delete_keeps_a_raster_another_job_uses_and_removes_the_last_one():
    _, drop = _hard_delete(1)
    assert not drop.called
    db, drop = _hard_delete(0)
    drop.assert_called_once()
    db.flush.assert_called()  # autoflush is off: rows deleted earlier in the cascade must not count


def test_hard_delete_guard_counts_only_live_jobs_of_the_same_tenant():
    db, _ = _hard_delete(0)
    conditions = db.query.return_value.filter.call_args.args
    text = " ".join(str(c) for c in conditions)
    assert "vegetation_jobs.tenant_id" in text and "vegetation_jobs.deleted_at IS NULL" in text
    assert "vegetation_jobs.id !=" in text
    tenant_cond = next(c for c in conditions if "vegetation_jobs.tenant_id" in str(c))
    assert TENANT in tenant_cond.compile().params.values()
