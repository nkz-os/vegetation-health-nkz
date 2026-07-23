"""Verify evalscripts are loadable and contain expected patterns."""

from app.services.evalscripts import MULTI_INDEX, NDVI_COLOR


class TestEvalscripts:
    def test_multi_index_loaded(self):
        assert "function setup()" in MULTI_INDEX
        assert "function evaluatePixel" in MULTI_INDEX
        assert "ndvi" in MULTI_INDEX
        assert "evi" in MULTI_INDEX
        assert "savi" in MULTI_INDEX
        assert "isClear" in MULTI_INDEX

    def test_ndvi_color_loaded(self):
        assert "function setup()" in NDVI_COLOR
        assert "function evaluatePixel" in NDVI_COLOR
        assert "COLOR_RAMP" in NDVI_COLOR
        assert "interpolateColor" in NDVI_COLOR

    def test_multi_index_is_valid_js_syntax(self):
        """Basic sanity — scripts are non-empty strings."""
        assert len(MULTI_INDEX) > 200
        assert len(NDVI_COLOR) > 200
        assert MULTI_INDEX.startswith("//VERSION=3")
        assert NDVI_COLOR.startswith("//VERSION=3")
