"""SAR band -> calibrated, georeferenced parcel raster in dB, on a real synthetic GCP raster."""
import math

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from shapely.geometry import box

from app.tasks import sar_tasks

LUT = b"""<calibration><calibrationVectorList count="2">
<calibrationVector><line>0</line><pixel count="2">0 199</pixel><sigmaNought count="2">1000 1000</sigmaNought></calibrationVector>
<calibrationVector><line>199</line><pixel count="2">0 199</pixel><sigmaNought count="2">1000 1000</sigmaNought></calibrationVector>
</calibrationVectorList></calibration>"""


def _grd(path, dn=500):
    """A GCP-only uint16 band like a GRD measurement: 200 x 200 over -1.6..-1.5 E, 42.8..42.9 N."""
    data = np.full((200, 200), dn, dtype=np.uint16)
    data[:, :10] = 0  # border without signal
    gcps = [GroundControlPoint(row=r, col=c, x=-1.6 + 0.1 * c / 199, y=42.9 - 0.1 * r / 199)
            for r in (0, 99, 199) for c in (0, 99, 199)]
    with rasterio.open(path, "w", driver="GTiff", height=200, width=200, count=1, dtype="uint16") as dst:
        dst.write(data, 1)
        dst.gcps = (gcps, "EPSG:4326")


def test_parcel_statistics_and_raster_are_calibrated_db(tmp_path):
    band, xml = tmp_path / "vv.tif", tmp_path / "vv.xml"
    _grd(band)
    xml.write_bytes(LUT)
    parcel = box(-1.57, 42.83, -1.53, 42.87)
    stats, raster = sar_tasks._calibrated_parcel(str(band), str(xml), parcel, str(tmp_path))
    expected = 10 * math.log10(500 ** 2 / 1000 ** 2)  # -6.02 dB
    assert math.isclose(stats["mean"], expected, abs_tol=1e-6) and stats["pixel_count"] > 0
    with rasterio.open(raster) as src:
        assert src.crs.to_string() == "EPSG:4326" and src.nodata == sar_tasks.SAR_DB_NODATA
        values = src.read(1)
    inside = values[values != sar_tasks.SAR_DB_NODATA]
    assert inside.size > 0 and np.allclose(inside, expected, atol=0.05)


def test_no_signal_pixels_are_left_out(tmp_path):
    band, xml = tmp_path / "vv.tif", tmp_path / "vv.xml"
    _grd(band, dn=0)
    xml.write_bytes(LUT)
    stats, _ = sar_tasks._calibrated_parcel(str(band), str(xml), box(-1.57, 42.83, -1.53, 42.87), str(tmp_path))
    assert stats["pixel_count"] == 0 and stats["mean"] is None
