"""Unit tests for SAR change detector."""
import pytest
from app.services.sar_change_detector import detect_change


def test_no_previous_returns_none():
    assert detect_change(-10.0, None) is None


def test_too_old_returns_none():
    assert detect_change(-10.0, -12.0, days_since_previous=25) is None


def test_small_delta_returns_none():
    result = detect_change(-10.0, -11.5)
    assert result is not None
    assert result["change_flag"] == "none"


def test_harvest_detected():
    result = detect_change(
        -16.0, -10.0,
        ndvi_current=0.25, ndvi_previous=0.60,
    )
    assert result is not None
    assert result["change_flag"] == "harvest"
    assert result["confidence"] == 0.85
    assert result["delta_vv"] == -6.0


def test_sowing_detected():
    result = detect_change(
        -7.0, -11.0,
        ndvi_current=0.30, ndvi_previous=0.30,
    )
    assert result is not None
    assert result["change_flag"] == "sowing"
    assert result["confidence"] == 0.80


def test_tillage_detected():
    result = detect_change(-7.0, -11.0)
    assert result is not None
    assert result["change_flag"] == "tillage"
    assert result["confidence"] == 0.75


def test_vegetation_change_masks_mechanical():
    # VV drop + large NDVI decline = harvest (crop removed, expected)
    result = detect_change(
        -16.0, -10.0,
        ndvi_current=0.20, ndvi_previous=0.70,
    )
    assert result is not None
    assert result["change_flag"] == "harvest"


def test_within_lookback_works():
    result = detect_change(-7.0, -11.0, days_since_previous=18)
    assert result is not None
    assert result["change_flag"] == "tillage"


def test_ndvi_change_without_vv_change_is_none():
    # VV stable + NDVI change = vegetation growth, not mechanical
    result = detect_change(
        -10.0, -10.5,
        ndvi_current=0.70, ndvi_previous=0.40,
    )
    assert result is not None
    assert result["change_flag"] == "none"
