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


def test_clip_reports_valid_fraction_of_parcel_pixels(tmp_path):
    parcel = box(-1.57, 42.83, -1.53, 42.87)
    full_band, xml = tmp_path / "full.tif", tmp_path / "vv.xml"
    _grd(full_band)
    xml.write_bytes(LUT)
    full, _ = sar_tasks._calibrated_parcel(str(full_band), str(xml), parcel, str(tmp_path))
    assert full["valid_fraction"] == 1.0 and full["parcel_pixels"] == full["pixel_count"]

    holed_band = tmp_path / "holed.tif"
    _grd(holed_band)
    with rasterio.open(holed_band, "r+") as dst:
        data = dst.read(1)
        data[:, 100:110] = 0  # a strip of the parcel without signal
        dst.write(data, 1)
    holed, _ = sar_tasks._calibrated_parcel(str(holed_band), str(xml), parcel, str(tmp_path))
    assert holed["parcel_pixels"] == full["parcel_pixels"]
    assert 0 < holed["pixel_count"] < holed["parcel_pixels"]
    assert holed["valid_fraction"] == holed["pixel_count"] / holed["parcel_pixels"] < 1.0


def test_valid_fraction_counts_the_whole_footprint_when_the_parcel_crosses_the_raster_edge(tmp_path):
    band, xml = tmp_path / "vv.tif", tmp_path / "vv.xml"
    _grd(band)  # covers -1.6 .. -1.5 E
    xml.write_bytes(LUT)
    inside = box(-1.59, 42.83, -1.55, 42.87)
    crossing = box(-1.63, 42.83, -1.55, 42.87)  # 3/8 of the width lies west of the raster
    a, _ = sar_tasks._calibrated_parcel(str(band), str(xml), inside, str(tmp_path))
    b, _ = sar_tasks._calibrated_parcel(str(band), str(xml), crossing, str(tmp_path))
    # same rows, 8 degrees-hundredths wide instead of 4: about twice the footprint pixels
    assert 1.8 < b["parcel_pixels"] / a["parcel_pixels"] < 2.2
    assert b["parcel_pixels"] > b["pixel_count"]
    assert b["valid_fraction"] == b["pixel_count"] / b["parcel_pixels"] < 0.65
