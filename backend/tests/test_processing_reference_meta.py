"""_order_bands_10m_first: ensure band_meta/reference_meta is driven by a
native-10 m band, so the saved COG/geometry-mask grid matches the 10 m index
array even when Sen2Res is off or its windowed fallback triggers.

For NDRE (required_bands=['B8A', 'B08']), B8A (20 m) is first by default;
this reorder must put B08 (10 m) first while leaving already-10m-first lists
(e.g. NDVI's ['B04', 'B08']) unchanged.
"""
import sys
from unittest.mock import MagicMock

# processing_tasks is imported via app.tasks.__init__, which transitively
# imports download_tasks, scheduler, storage_cleanup, sar_tasks and
# historical_baseline — stub the heavy/native deps not under test so the
# import graph resolves without installing them. Mirrors test_window_band_set.py.
for _m in ("rasterio", "rasterio.warp", "rasterio.features", "shapely",
           "shapely.geometry", "shapely.ops", "geoalchemy2", "geoalchemy2.shape",
           "simpleeval", "redis", "redis.exceptions", "celery",
           "celery.schedules", "celery.signals", "kombu"):
    sys.modules.setdefault(_m, MagicMock())

from app.tasks.processing_tasks import _order_bands_10m_first


def test_ndre_20m_band_moved_after_10m():
    assert _order_bands_10m_first(['B8A', 'B08']) == ['B08', 'B8A']


def test_ndvi_already_10m_first_unchanged():
    assert _order_bands_10m_first(['B04', 'B08']) == ['B04', 'B08']


def test_all_10m_bands_stable_order():
    assert _order_bands_10m_first(['B02', 'B04', 'B08']) == ['B02', 'B04', 'B08']
