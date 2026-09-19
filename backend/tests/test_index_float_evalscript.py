"""Tests for parameterized FLOAT32 index evalscript for Process API."""

import pytest
from app.services.evalscripts import build_index_float


def test_build_index_float_injects_index_and_has_required_fixes():
    js = build_index_float("NDVI")
    assert "NDVI" in js
    # SCL must be DN via per-band units array (not the string "reflectance" alone)
    assert '"DN"' in js or "'DN'" in js
    assert "dataMask" in js  # required output
    assert "SIMPLE" in js  # mosaicking
    assert "ORBIT" not in js


def test_build_index_float_supports_all_five():
    for idx in ("NDVI", "EVI", "SAVI", "GNDVI", "NDRE"):
        assert idx in build_index_float(idx)


def test_build_index_float_rejects_unsupported():
    with pytest.raises(ValueError, match="NDMI"):
        build_index_float("NDMI")
