"""compute_smi on the calibrated parcel raster: its nodata marker is not backscatter."""
import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.services.sar_smi_processor import compute_smi


def test_nodata_pixels_are_left_out_of_the_smi(tmp_path):
    path = tmp_path / "vv_db.tif"
    data = np.full((20, 20), -10.0, dtype=np.float32)
    data[:, :5] = -9999.0  # no signal
    with rasterio.open(path, "w", driver="GTiff", height=20, width=20, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(-1.6, 42.9, 0.001, 0.001),
                       nodata=-9999.0) as dst:
        dst.write(data, 1)
    parcel = {"type": "Polygon", "coordinates": [[[-1.6, 42.88], [-1.58, 42.88], [-1.58, 42.9],
                                                  [-1.6, 42.9], [-1.6, 42.88]]]}
    out = compute_smi(str(path), parcel)
    assert out["pixel_count"] == 15 * 20
    assert 0.0 < out["mean"] < 1.0
