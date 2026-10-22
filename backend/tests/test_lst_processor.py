"""Unit tests for Landsat LST processor (DN conversion and zonal stats)."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
import tempfile
import os

from app.services.lst_processor import _dn_to_celsius, compute_lst_zonal_stats


def test_dn_to_celsius_usgs_scaling():
    # DN=10000 → K = 10000*0.00341802+149 ≈ 183.18 → °C ≈ -89.97
    arr = np.array([[10000.0, 0.0, 65535.0]], dtype=np.float32)
    out = _dn_to_celsius(arr)
    assert np.isnan(out[0, 1])
    assert np.isnan(out[0, 2])
    assert out[0, 0] == pytest.approx(183.1802 - 273.15, abs=0.01)


def test_compute_lst_zonal_stats_over_parcel():
    # 3x3 raster, 30°C in center pixels
    dn_val = (30.0 + 273.15 - 149.0) / 0.00341802
    data = np.full((1, 3, 3), dn_val, dtype=np.float32)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "lst.tif")
        transform = from_origin(0.0, 0.003, 0.001, 0.001)
        with rasterio.open(
            path, "w", driver="GTiff", height=3, width=3, count=1,
            dtype="float32", crs="EPSG:4326", transform=transform,
        ) as dst:
            dst.write(data)

        geom = {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [0.003, 0.0], [0.003, 0.003], [0.0, 0.003], [0.0, 0.0]]],
        }
        stats = compute_lst_zonal_stats(path, geom)
        assert stats["pixel_count"] > 0
        assert stats["mean"] == pytest.approx(30.0, abs=0.5)
