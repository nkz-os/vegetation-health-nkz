"""_window_band_set: include Sen2Res guides only when a 20 m target is needed."""
import sys
from unittest.mock import MagicMock

# processing_tasks is imported via app.tasks.__init__, which transitively
# imports download_tasks, scheduler, storage_cleanup, sar_tasks and
# historical_baseline — stub the heavy/native deps not under test so the
# import graph resolves without installing them.
for _m in ("rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
           "rasterio.transform", "rasterio.crs", "rasterio.merge", "rasterio.mask", "shapely",
           "shapely.geometry", "shapely.ops", "geoalchemy2", "geoalchemy2.shape",
           "simpleeval", "redis", "redis.exceptions", "celery",
           "celery.schedules", "celery.signals", "kombu"):
    sys.modules.setdefault(_m, MagicMock())

from app.tasks.processing_tasks import _window_band_set

_SCENE = {"B02": "p", "B03": "p", "B04": "p", "B08": "p", "B8A": "p", "SCL": "p"}


def test_ndvi_no_targets_no_extra_guides():
    # NDVI uses B04+B08 (both 10 m, no target band) → no guide expansion.
    assert _window_band_set(["B04", "B08"], _SCENE, True) == ["B04", "B08"]


def test_ndre_target_pulls_present_guides():
    # NDRE uses B8A (a 20 m target) → add guides present in the scene.
    out = _window_band_set(["B8A", "B08"], _SCENE, True)
    assert set(out) >= {"B8A", "B08", "B02", "B03", "B04"}
    assert len(out) == len(set(out))  # deduplicated


def test_disabled_never_expands():
    assert _window_band_set(["B8A", "B08"], _SCENE, False) == ["B8A", "B08"]
