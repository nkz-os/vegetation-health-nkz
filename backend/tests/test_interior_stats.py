"""Interior statistics: pure mask and statistics functions over the index raster."""
import numpy as np
from pyproj import Transformer
from rasterio.transform import from_origin

from app.services import interior_stats as ist

CRS = "EPSG:32630"
X0, Y0 = 500000.0, 4600000.0


def _geojson(x_min, y_min, x_max, y_max):
    """Square given in UTM metres, returned as a WGS84 GeoJSON polygon."""
    to_wgs = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    ring = [to_wgs.transform(x, y) for x, y in
            [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max), (x_min, y_min)]]
    return {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}


def _meta(res, size_px):
    return {"crs": CRS, "transform": from_origin(X0, Y0, res, res), "width": size_px, "height": size_px}


def test_core_excludes_border():
    # 100 x 100 m parcel on a 10 m grid: 10 x 10 pixels, 8 x 8 once 10 m are removed.
    parcel = _geojson(X0, Y0 - 100, X0 + 100, Y0)
    inside, name = ist.interior_mask(_meta(10, 20), parcel, (20, 20))
    assert name == "core"
    assert inside.dtype == bool and inside.shape == (20, 20)
    assert inside.sum() == 64
    assert inside[1:9, 1:9].all()
    assert not inside[0, :].any() and not inside[9, :].any()
    assert not inside[:, 0].any() and not inside[:, 9].any()
    assert ist.parcel_mask(_meta(10, 20), parcel, (20, 20)).sum() == 100


def test_small_parcel_falls_back_to_parcel():
    # 60 x 60 m: the core would be 4 x 4 = 16 pixels (< 20) -> exact parcel, 36 pixels.
    parcel = _geojson(X0, Y0 - 60, X0 + 60, Y0)
    inside, name = ist.interior_mask(_meta(10, 20), parcel, (20, 20))
    assert name == "parcel"
    assert inside.sum() == 36


def test_parcel_without_pixels_gives_empty_mask():
    parcel = _geojson(X0 + 6, Y0 - 9, X0 + 9, Y0 - 6)  # no pixel centre inside
    inside, name = ist.interior_mask(_meta(10, 20), parcel, (20, 20))
    assert name == "parcel" and inside.sum() == 0
    assert ist.interior_statistics(np.ones((20, 20), dtype=np.float32), inside, name) is None


def test_stats_values():
    arr = np.full((4, 4), np.nan, dtype=np.float32)
    arr[:2, :] = np.arange(1, 9, dtype=np.float32).reshape(2, 4)  # 1..8, rest NaN
    inside = np.ones((4, 4), dtype=bool)
    inside[3, :] = False  # last row outside: ignored even if it had values
    arr[3, :] = 100.0
    s = ist.interior_statistics(arr, inside, "core")
    assert s["total_pixels"] == 12 and s["valid_pixels"] == 8
    assert s["valid_fraction"] == 8 / 12
    assert s["median"] == 4.5 and s["mean"] == 4.5
    assert s["p10"] == float(np.percentile(np.arange(1, 9), 10))
    assert s["p90"] == float(np.percentile(np.arange(1, 9), 90))
    assert s["mask"] == "core"


def test_all_nan_inside_gives_no_values():
    arr = np.full((3, 3), np.nan, dtype=np.float32)
    s = ist.interior_statistics(arr, np.ones((3, 3), dtype=bool), "parcel")
    assert s["valid_pixels"] == 0 and s["total_pixels"] == 9 and s["valid_fraction"] == 0.0
    assert s["median"] is None and s["mean"] is None and s["p10"] is None and s["p90"] is None


def test_mask_resampled_to_index_shape():
    # Grid of band_meta is 20 m (20 x 20 px); the index is at 10 m (40 x 40 px).
    parcel = _geojson(X0 + 1, Y0 - 301, X0 + 301, Y0 - 1)
    inside, name = ist.interior_mask(_meta(20, 20), parcel, (40, 40))
    assert inside.shape == (40, 40) and name == "core"
    assert inside.sum() > 0 and inside.sum() % 4 == 0  # each 20 m pixel -> 2 x 2 index pixels
    assert ist.parcel_mask(_meta(20, 20), parcel, (40, 40)).shape == (40, 40)


def test_cloud_fraction_classes():
    inside = np.ones((2, 5), dtype=bool)
    scl = np.array([[3, 8, 9, 10, 4], [7, 11, 5, 6, 3]])
    # 3, 8, 9, 10 and 3 count; 7 and 11 do not.
    assert ist.cloud_fraction(scl, inside) == 5 / 10
    inside[1, :] = False
    assert ist.cloud_fraction(scl, inside) == 4 / 5
    assert ist.cloud_fraction(None, inside) is None
    assert ist.cloud_fraction(scl, np.zeros((2, 5), dtype=bool)) is None
