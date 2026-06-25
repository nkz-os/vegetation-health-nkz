"""Tests for window_crop — real rasterio, synthetic UTM GeoTIFFs (no S2 data)."""
import importlib.util
import os
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from pyproj import Transformer

# Import window_crop.py directly, bypassing app.services.__init__ which pulls
# fastapi/redis/geoalchemy2. Mirrors tests/test_superresolution.py.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "services", "window_crop.py"
)
_spec = importlib.util.spec_from_file_location("window_crop", _MODULE_PATH)
_wc = importlib.util.module_from_spec(_spec)
sys.modules["window_crop"] = _wc
_spec.loader.exec_module(_wc)
crop_bands_to_window = _wc.crop_bands_to_window

# UTM zone 30N tile origin (top-left), 10 m pixels, 100x100 = 1 km square.
_CRS = "EPSG:32630"
_ORIGIN_X, _ORIGIN_Y = 500000.0, 4600000.0  # x left, y top
_RES = 10.0
_SIZE = 100


def _write_band(path, crs=_CRS, res=_RES, size=_SIZE):
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, res, res)
    data = np.arange(size * size, dtype=np.float32).reshape(size, size)
    profile = dict(
        driver="GTiff", height=size, width=size, count=1,
        dtype="float32", crs=crs, transform=transform,
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def _utm_subextent_to_4326_geojson(ux0, uy0, ux1, uy1):
    """Inverse-project a UTM sub-extent to a 4326 polygon (parcel bounds)."""
    tr = Transformer.from_crs(_CRS, "EPSG:4326", always_xy=True)
    xs, ys = tr.transform([ux0, ux0, ux1, ux1], [uy0, uy1, uy0, uy1])
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    return {
        "type": "Polygon",
        "coordinates": [[
            [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
        ]],
    }


def test_window_size_transform_and_crs(tmp_path):
    band = str(tmp_path / "B04.tif")
    _write_band(band)
    # A 200 m × 200 m parcel: UTM x[500200, 500400], y[4599600, 4599800].
    geojson = _utm_subextent_to_4326_geojson(500200, 4599600, 500400, 4599800)

    out = crop_bands_to_window({"B04": band}, geojson, buffer_m=0.0,
                               output_dir=str(tmp_path / "win"))

    with rasterio.open(out["B04"]) as src:
        assert 18 <= src.width <= 22      # ~20 px (200 m / 10 m), ±1 rounding
        assert 18 <= src.height <= 22
        assert str(src.crs) == _CRS       # CRS preserved
        # Top-left of the windowed transform maps to the parcel's UTM corner.
        assert src.transform.c == pytest.approx(500200, abs=15)   # x left
        assert src.transform.f == pytest.approx(4599800, abs=15)  # y top
        assert src.transform.a == pytest.approx(_RES)             # pixel size kept


def test_buffer_expands_window(tmp_path):
    band = str(tmp_path / "B04.tif")
    _write_band(band)
    geojson = _utm_subextent_to_4326_geojson(500200, 4599600, 500400, 4599800)

    no_buf = crop_bands_to_window({"B04": band}, geojson, buffer_m=0.0,
                                  output_dir=str(tmp_path / "a"))
    buf = crop_bands_to_window({"B04": band}, geojson, buffer_m=100.0,
                               output_dir=str(tmp_path / "b"))
    with rasterio.open(no_buf["B04"]) as s0, rasterio.open(buf["B04"]) as s1:
        # +100 m on every side at 10 m/px ≈ +20 px width and height.
        assert s1.width >= s0.width + 18
        assert s1.height >= s0.height + 18


def test_clamped_at_band_edge(tmp_path):
    band = str(tmp_path / "B04.tif")
    _write_band(band)
    # Parcel at the top-left corner + huge buffer → must clamp, not overflow.
    geojson = _utm_subextent_to_4326_geojson(500000, 4599900, 500100, 4600000)
    out = crop_bands_to_window({"B04": band}, geojson, buffer_m=100000.0,
                               output_dir=str(tmp_path / "c"))
    with rasterio.open(out["B04"]) as src:
        assert src.width <= _SIZE
        assert src.height <= _SIZE


def test_negative_buffer_collapses_window_raises(tmp_path):
    band = str(tmp_path / "B04.tif")
    _write_band(band)
    # Valid 200 m parcel, but a -150 m buffer (> half the 200 m extent on each
    # side) collapses the window: pminx > pmaxx. Must surface as ValueError,
    # not an uncaught rasterio WindowError from window construction.
    geojson = _utm_subextent_to_4326_geojson(500200, 4599600, 500400, 4599800)
    with pytest.raises(ValueError):
        crop_bands_to_window({"B04": band}, geojson, buffer_m=-150.0,
                             output_dir=str(tmp_path / "e"))


def test_multiband_10m_and_20m_same_extent(tmp_path):
    # Production passes B04 (10 m) + B8A (20 m) together for the same parcel.
    # Both must cover the same geographic extent, sharing the CRS, with the
    # 20 m output at ~half the pixel dimensions of the 10 m one.
    band_10m = str(tmp_path / "B04.tif")
    band_20m = str(tmp_path / "B8A.tif")
    _write_band(band_10m, res=10.0, size=100)
    _write_band(band_20m, res=20.0, size=50)
    geojson = _utm_subextent_to_4326_geojson(500200, 4599600, 500400, 4599800)

    out = crop_bands_to_window(
        {"B04": band_10m, "B8A": band_20m}, geojson, buffer_m=0.0,
        output_dir=str(tmp_path / "win"),
    )

    assert "B04" in out and "B8A" in out
    with rasterio.open(out["B04"]) as s10, rasterio.open(out["B8A"]) as s20:
        assert str(s10.crs) == str(s20.crs) == _CRS
        # Same top-left corner (within rounding tolerance).
        assert s10.transform.c == pytest.approx(s20.transform.c, abs=15)
        assert s10.transform.f == pytest.approx(s20.transform.f, abs=15)
        # 20 m output has ~half the pixel dimensions of the 10 m one.
        assert s20.width == pytest.approx(s10.width / 2, abs=1)
        assert s20.height == pytest.approx(s10.height / 2, abs=1)


def test_empty_window_raises(tmp_path):
    band = str(tmp_path / "B04.tif")
    _write_band(band)
    # A parcel ~30 km east of the band footprint.
    geojson = _utm_subextent_to_4326_geojson(530000, 4599600, 530200, 4599800)
    with pytest.raises(ValueError):
        crop_bands_to_window({"B04": band}, geojson, buffer_m=0.0,
                             output_dir=str(tmp_path / "d"))
