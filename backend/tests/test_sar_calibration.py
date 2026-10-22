"""Sentinel-1 GRD radiometric calibration: DN -> sigma0 with the product's calibration LUT."""
import math

import numpy as np

from app.services import sar_calibration as cal

# Shape of a real S1 calibration annotation (calibrationVectorList), two vectors, two pixels each.
XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<calibration>
  <calibrationVectorList count="2">
    <calibrationVector>
      <azimuthTime>2025-05-12T17:54:36.000000</azimuthTime>
      <line>0</line>
      <pixel count="2">0 100</pixel>
      <sigmaNought count="2">500 700</sigmaNought>
      <betaNought count="2">450 450</betaNought>
      <gamma count="2">480 650</gamma>
      <dn count="2">450 450</dn>
    </calibrationVector>
    <calibrationVector>
      <azimuthTime>2025-05-12T17:54:46.000000</azimuthTime>
      <line>100</line>
      <pixel count="2">0 100</pixel>
      <sigmaNought count="2">600 800</sigmaNought>
      <betaNought count="2">450 450</betaNought>
      <gamma count="2">580 750</gamma>
      <dn count="2">450 450</dn>
    </calibrationVector>
  </calibrationVectorList>
</calibration>"""


def test_the_lut_is_read_from_the_calibration_annotation():
    lut = cal.parse_calibration(XML)
    assert list(lut.lines) == [0, 100]
    assert list(lut.pixels[0]) == [0, 100] and list(lut.sigma[1]) == [600, 800]


def test_the_coefficient_is_bilinear_in_line_and_pixel():
    lut = cal.parse_calibration(XML)
    a = cal.sigma_coefficient(lut, np.array([0, 0, 50, 100]), np.array([0, 50, 50, 100]))
    assert np.allclose(a, [500, 600, 650, 800])


def test_sigma0_is_dn_squared_over_the_coefficient_squared():
    # ESA (ESA-EOPG-CSCOP-TN-0002): sigma0 = |DN|^2 / A_sigma^2
    lut = cal.parse_calibration(XML)
    db = cal.sigma0_db(np.array([500.0, 50.0, 0.0]), np.array([0, 0, 0]), np.array([0, 0, 0]), lut)
    assert math.isclose(db[0], 0.0, abs_tol=1e-9)
    assert math.isclose(db[1], -20.0, abs_tol=1e-9)
    assert math.isnan(db[2])  # no signal (DN 0) is not a backscatter value


def test_zonal_mean_is_taken_in_linear_power():
    stats = cal.zonal_db(np.array([0.0, -10.0]))
    assert math.isclose(stats["mean"], 10 * math.log10((1.0 + 0.1) / 2), abs_tol=1e-9)
    assert stats["min"] == -10.0 and stats["max"] == 0.0 and stats["pixel_count"] == 2
