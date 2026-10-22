"""Sentinel-2 L2A digital numbers must become reflectance before any index.

Since processing baseline 04.00 (Jan 2022) L2A stores 10000*rho + 1000
(BOA_ADD_OFFSET = -1000); before it, 10000*rho. Indices computed on the raw
numbers are biased (NDVI of rho 0.45/0.025 reads 0.63 instead of 0.89).
"""
import numpy as np
import pytest

from app.services.processor import VegetationIndexProcessor
from app.services.radiometry import L2ARadiometry, l2a_radiometry


def test_baseline_04_and_later_carry_the_offset():
    r = l2a_radiometry("S2B_MSIL2A_20260603T105619_N0511_R094_T30TXM_20260603T134502")
    assert r == L2ARadiometry(offset=-1000.0, quantification=10000.0)


def test_older_baselines_have_no_offset():
    assert l2a_radiometry("S2A_MSIL2A_20210603T105621_N0300_R051_T30TWN_20210603T133012.SAFE").offset == 0.0


def test_unknown_product_id_is_rejected():
    with pytest.raises(ValueError):
        l2a_radiometry("S2A_TEST")


def _processor(bands, radiometry):
    p = VegetationIndexProcessor.__new__(VegetationIndexProcessor)
    p.band_paths, p.band_data, p.bbox, p.radiometry = {}, {}, None, radiometry
    p.band_meta = {"transform": None, "crs": "EPSG:4326", "width": 5, "height": 5, "count": 1}
    p._raw = bands
    return p


def test_reflectance_conversion_and_nodata():
    r = l2a_radiometry("S2B_MSIL2A_20260603T105619_N0511_R094_T30TXM_20260603T134502")
    dn = np.array([[5500.0, 0.0]], dtype=np.float32)   # rho 0.45 and NO_DATA
    out = VegetationIndexProcessor.to_reflectance("B08", dn, r)
    assert out[0, 0] == pytest.approx(0.45) and np.isnan(out[0, 1])
    scl = np.array([[4.0, 8.0]], dtype=np.float32)
    assert np.array_equal(VegetationIndexProcessor.to_reflectance("SCL", scl, r), scl)  # categorical: untouched


def test_index_on_digital_numbers_matches_reflectance():
    r = l2a_radiometry("S2B_MSIL2A_20260603T105619_N0511_R094_T30TXM_20260603T134502")
    p = _processor({}, r)
    p.band_data = {"B04": VegetationIndexProcessor.to_reflectance("B04", np.full((5, 5), 1250.0, np.float32), r),
                   "B08": VegetationIndexProcessor.to_reflectance("B08", np.full((5, 5), 5500.0, np.float32), r)}
    p.load_bands = lambda bands: None
    ndvi = p.calculate_ndvi(apply_cloud_mask=False)
    assert ndvi[0, 0] == pytest.approx((0.45 - 0.025) / (0.45 + 0.025), abs=1e-4)
