"""Multi-date zoning: every date lands on the same parcel grid."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from pyproj import Transformer
from shapely.geometry import Polygon, mapping

from app.jobs.zoning_multidate.grid import parcel_grid, read_onto_grid

# Synthetic parcel near 42.8 N, -1.65 E: ~200 m x 150 m.
PARCEL = Polygon([(-1.6510, 42.8000), (-1.6486, 42.8000), (-1.6486, 42.8014), (-1.6510, 42.8014)])


def _field(x, y):
    """Smooth synthetic index: increases west to east."""
    return 0.3 + (x - 597000.0) / 2000.0


def _write_utm(path, buffer_m=2560):
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    minx, miny = to_utm.transform(-1.6510, 42.8000)
    maxx, maxy = to_utm.transform(-1.6486, 42.8014)
    x0 = np.floor((minx - 50) / 10) * 10
    y0 = np.ceil((maxy + 50) / 10) * 10
    w = int((maxx - x0 + 50) // 10) + 1
    h = int((y0 - miny + 50) // 10) + 1
    xs = x0 + 5 + 10 * np.arange(w)
    data = np.repeat(_field(xs, 0)[None, :], h, axis=0).astype("float32")
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                       crs="EPSG:32630", transform=from_origin(x0, y0, 10, 10), nodata=-9999) as dst:
        dst.write(data, 1)


def _write_4326(path):
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    res = 0.0001
    lons = -1.6515 + res * (np.arange(40) + 0.5)
    lats = 42.8019 - res * (np.arange(30) + 0.5)
    lon_g, lat_g = np.meshgrid(lons, lats)
    x, _ = to_utm.transform(lon_g, lat_g)
    data = _field(x, 0).astype("float32")
    with rasterio.open(path, "w", driver="GTiff", height=30, width=40, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(-1.6515, 42.8019, res, res)) as dst:
        dst.write(data, 1)


def test_utm_and_alignment():
    g = parcel_grid(mapping(PARCEL), 10.0)
    assert g.crs == "EPSG:32630"
    assert g.transform.a == 10.0 and g.transform.e == -10.0
    assert g.transform.c % 10 == 0 and g.transform.f % 10 == 0
    assert g.inside.shape == g.shape and g.inside.any()


def test_copernicus_4326_and_local_utm_land_on_same_grid(tmp_path):
    g = parcel_grid(mapping(PARCEL), 10.0)
    _write_utm(tmp_path / "local.tif")
    _write_4326(tmp_path / "cop.tif")
    a = read_onto_grid(str(tmp_path / "local.tif"), g)
    b = read_onto_grid(str(tmp_path / "cop.tif"), g)
    inside = g.inside & ~np.isnan(a) & ~np.isnan(b)
    assert inside.sum() > 0.8 * g.inside.sum()
    assert float(np.mean(np.abs(a[inside] - b[inside]))) < 0.02


def test_minus9999_and_nan_become_nan(tmp_path):
    g = parcel_grid(mapping(PARCEL), 10.0)
    _write_utm(tmp_path / "local.tif")
    with rasterio.open(tmp_path / "local.tif", "r+") as ds:
        arr = ds.read(1)
        arr[:, : arr.shape[1] // 2] = -9999
        arr[0:2, :] = np.nan
        ds.write(arr, 1)
    out = read_onto_grid(str(tmp_path / "local.tif"), g)
    assert not np.any(out == -9999)
    assert np.isnan(out[g.inside]).any()
    assert (np.nanmin(out[g.inside]) > -1.0)


def test_reads_bytes(tmp_path):
    g = parcel_grid(mapping(PARCEL), 10.0)
    _write_utm(tmp_path / "local.tif")
    out = read_onto_grid((tmp_path / "local.tif").read_bytes(), g)
    assert out.shape == g.shape


def test_invalid_parcel_is_repaired():
    bowtie = {"type": "Polygon", "coordinates": [[
        [-1.6510, 42.8000], [-1.6486, 42.8014], [-1.6486, 42.8000], [-1.6510, 42.8014], [-1.6510, 42.8000],
    ]]}
    g = parcel_grid(bowtie, 10.0)
    assert g.parcel_utm.is_valid and g.parcel_utm.area > 0
