"""_is_raw_cache_entry: super-resolved cache entries are not reusable as raw."""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# download_tasks imports heavy modules at import time; stub the ones not under
# test so the helper can be imported in isolation.
for _m in ("rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
           "rasterio.transform", "rasterio.crs", "rasterio.merge", "rasterio.mask", "shapely",
           "shapely.geometry", "shapely.ops", "geoalchemy2", "geoalchemy2.shape",
           "simpleeval"):
    sys.modules.setdefault(_m, MagicMock())

from app.tasks.download_tasks import _is_raw_cache_entry


def test_sen2res_entry_not_reusable():
    entry = SimpleNamespace(quality_flags={"sen2res_applied": True})
    assert _is_raw_cache_entry(entry) is False


def test_raw_entry_reusable():
    entry = SimpleNamespace(quality_flags={"sen2res_applied": False})
    assert _is_raw_cache_entry(entry) is True


def test_missing_flags_treated_as_raw():
    assert _is_raw_cache_entry(SimpleNamespace(quality_flags=None)) is True
