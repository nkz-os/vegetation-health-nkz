"""Test scene selection logic — least cloudy, most recent date."""
import sys
from unittest.mock import MagicMock

# Mock missing external deps before importing task modules
# (geoalchemy2, rasterio, shapely, simpleeval are not in test env)
_MOCK_MODULES = [
    'geoalchemy2', 'geoalchemy2.shape', 'geoalchemy2.types',
    'simpleeval',
    'rasterio', 'rasterio.warp', 'rasterio.features',
    'rasterio.windows', 'rasterio.transform', 'rasterio.crs',
    'rasterio.merge', 'rasterio.mask',
    'shapely', 'shapely.geometry', 'shapely.ops',
    's2cloudless', 's2cloudless.utils',
    'opencv', 'cv2',
]
# The stubs exist only so `download_tasks` can be imported without the heavy geo
# stack. They MUST be undone afterwards: leaving MagicMocks in sys.modules under
# `shapely` and `rasterio` poisons every test file imported later in the run,
# which is why window_crop, superresolution and the SAR geometry tests all failed
# in a full-suite run while passing on their own.
_ORIGINAL_MODULES = {_mod: sys.modules.get(_mod) for _mod in _MOCK_MODULES}
for _mod in _MOCK_MODULES:
    sys.modules[_mod] = MagicMock()

from app.tasks.download_tasks import _select_best_scene  # noqa: E402

# `_select_best_scene` is already bound, so the real modules can come back.
for _mod, _original in _ORIGINAL_MODULES.items():
    if _original is None:
        sys.modules.pop(_mod, None)
    else:
        sys.modules[_mod] = _original


def test_picks_least_cloudy_scene():
    scenes = [
        {"id": "s1", "cloud_cover": 80, "sensing_date": "2026-01-01"},
        {"id": "s2", "cloud_cover": 10, "sensing_date": "2026-01-02"},
        {"id": "s3", "cloud_cover": 50, "sensing_date": "2026-01-03"},
    ]
    best = _select_best_scene(scenes)
    assert best["id"] == "s2", f"Expected s2 (10% cloud), got {best['id']}"


def test_tie_break_by_most_recent_date():
    """For agronomic monitoring, prefer the newest image when cloud cover ties."""
    scenes = [
        {"id": "old", "cloud_cover": 10, "sensing_date": "2026-01-01"},
        {"id": "new", "cloud_cover": 10, "sensing_date": "2026-06-10"},
    ]
    best = _select_best_scene(scenes)
    assert best["id"] == "new", f"Expected 'new' (most recent), got {best['id']}"
