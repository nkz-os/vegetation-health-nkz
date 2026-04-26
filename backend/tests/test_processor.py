"""
Tests for VegetationIndexProcessor — core index calculation logic.
Uses synthetic numpy arrays to verify formula correctness without rasterio I/O.
"""
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

# Mock rasterio before importing processor
import sys
sys.modules['rasterio'] = MagicMock()
sys.modules['rasterio.warp'] = MagicMock()
sys.modules['rasterio.features'] = MagicMock()
sys.modules['rasterio.windows'] = MagicMock()
sys.modules['rasterio.transform'] = MagicMock()
sys.modules['shapely'] = MagicMock()
sys.modules['shapely.geometry'] = MagicMock()
sys.modules['simpleeval'] = MagicMock()

from app.services.processor import VegetationIndexProcessor


class TestVegetationIndexProcessor:
    """Test index formula correctness with synthetic numpy arrays."""

    def _setup_processor(self, band_data: dict):
        """Inject band data directly into the processor, bypassing rasterio."""
        processor = VegetationIndexProcessor.__new__(VegetationIndexProcessor)
        processor.band_paths = {}
        processor.band_data = band_data
        processor.band_meta = {
            'transform': None,
            'crs': 'EPSG:4326',
            'width': 100,
            'height': 100,
            'count': 1,
        }
        processor.bbox = None
        return processor

    # ---- NDVI Tests ----

    def test_ndvi_healthy_vegetation(self):
        """NDVI for dense vegetation: NIR=0.5, Red=0.1 → (0.5-0.1)/(0.5+0.1) = 0.667"""
        nir = np.full((10, 10), 0.5, dtype=np.float32)
        red = np.full((10, 10), 0.1, dtype=np.float32)
        p = self._setup_processor({'B04': red, 'B08': nir})
        ndvi = p.calculate_ndvi(apply_cloud_mask=False)
        expected = 0.4 / 0.6  # 0.666...
        assert np.allclose(ndvi, expected, atol=1e-4)

    def test_ndvi_bare_soil(self):
        """NDVI for bare soil: NIR=0.2, Red=0.2 → (0.2-0.2)/(0.2+0.2) = 0.0"""
        nir = np.full((10, 10), 0.2, dtype=np.float32)
        red = np.full((10, 10), 0.2, dtype=np.float32)
        p = self._setup_processor({'B04': red, 'B08': nir})
        ndvi = p.calculate_ndvi(apply_cloud_mask=False)
        assert np.allclose(ndvi, 0.0, atol=1e-4)

    def test_ndvi_water(self):
        """NDVI for water: NIR=0.02, Red=0.05 → negative"""
        nir = np.full((10, 10), 0.02, dtype=np.float32)
        red = np.full((10, 10), 0.05, dtype=np.float32)
        p = self._setup_processor({'B04': red, 'B08': nir})
        ndvi = p.calculate_ndvi(apply_cloud_mask=False)
        expected = (0.02 - 0.05) / (0.02 + 0.05)  # -0.4286
        assert np.allclose(ndvi, expected, atol=1e-4)

    def test_ndvi_zero_division(self):
        """NDVI handles NIR+Red=0 gracefully → returns 0."""
        nir = np.full((5, 5), 0.0, dtype=np.float32)
        red = np.full((5, 5), 0.0, dtype=np.float32)
        p = self._setup_processor({'B04': red, 'B08': nir})
        ndvi = p.calculate_ndvi(apply_cloud_mask=False)
        assert np.all(ndvi == 0.0)

    def test_ndvi_clipped_to_range(self):
        """NDVI values outside [-1, 1] are clipped."""
        nir = np.full((5, 5), 2.0, dtype=np.float32)
        red = np.full((5, 5), -0.5, dtype=np.float32)
        p = self._setup_processor({'B04': red, 'B08': nir})
        ndvi = p.calculate_ndvi(apply_cloud_mask=False)
        assert np.all(ndvi >= -1.0) and np.all(ndvi <= 1.0)

    # ---- EVI Tests ----

    def test_evi_formula(self):
        """EVI = 2.5 * (NIR-Red) / (NIR + 6*Red - 7.5*Blue + 1)"""
        nir = np.full((5, 5), 0.5, dtype=np.float32)
        red = np.full((5, 5), 0.1, dtype=np.float32)
        blue = np.full((5, 5), 0.05, dtype=np.float32)
        p = self._setup_processor({'B02': blue, 'B04': red, 'B08': nir})
        evi = p.calculate_evi(apply_cloud_mask=False)
        denom = 0.5 + 6 * 0.1 - 7.5 * 0.05 + 1  # 0.5 + 0.6 - 0.375 + 1 = 1.725
        expected = 2.5 * (0.5 - 0.1) / denom  # 2.5 * 0.4 / 1.725 = 0.5797
        assert np.allclose(evi, expected, atol=1e-4)

    # ---- SAVI Tests ----

    def test_savi_formula_default_l(self):
        """SAVI = ((NIR-Red)/(NIR+Red+L)) * (1+L) with L=0.5"""
        nir = np.full((5, 5), 0.5, dtype=np.float32)
        red = np.full((5, 5), 0.1, dtype=np.float32)
        p = self._setup_processor({'B04': red, 'B08': nir})
        savi = p.calculate_savi(l=0.5, apply_cloud_mask=False)
        # ((0.4)/(0.5+0.1+0.5)) * 1.5 = (0.4/1.1)*1.5 = 0.54545
        expected = (0.4 / 1.1) * 1.5
        assert np.allclose(savi, expected, atol=1e-4)

    def test_savi_bare_soil_l1(self):
        """SAVI with L=1 for very sparse vegetation."""
        nir = np.full((5, 5), 0.2, dtype=np.float32)
        red = np.full((5, 5), 0.2, dtype=np.float32)
        p = self._setup_processor({'B04': red, 'B08': nir})
        savi = p.calculate_savi(l=1.0, apply_cloud_mask=False)
        assert np.allclose(savi, 0.0, atol=1e-4)

    # ---- GNDVI Tests ----

    def test_gndvi_formula(self):
        """GNDVI = (NIR-Green)/(NIR+Green)"""
        nir = np.full((5, 5), 0.5, dtype=np.float32)
        green = np.full((5, 5), 0.1, dtype=np.float32)
        p = self._setup_processor({'B03': green, 'B08': nir})
        gndvi = p.calculate_gndvi(apply_cloud_mask=False)
        expected = 0.4 / 0.6
        assert np.allclose(gndvi, expected, atol=1e-4)

    # ---- Statistics Tests ----

    def test_calculate_statistics(self):
        """Statistics computed correctly on uniform array."""
        data = np.full((100, 100), 0.5, dtype=np.float32)
        p = self._setup_processor({})
        stats = p.calculate_statistics(data)
        assert stats['mean'] == 0.5
        assert stats['min'] == 0.5
        assert stats['max'] == 0.5
        assert stats['std'] == 0.0
        assert stats['pixel_count'] == 10000

    def test_calculate_statistics_with_nan(self):
        """Statistics ignore NaN values."""
        data = np.array([[0.1, 0.2, np.nan], [0.3, np.nan, 0.4]], dtype=np.float32)
        p = self._setup_processor({})
        stats = p.calculate_statistics(data)
        assert stats['pixel_count'] == 4
        assert np.isclose(stats['mean'], 0.25)

    # ---- Temporal Composite Tests ----

    def test_temporal_composite_median(self):
        """Temporal composite using median across scenes."""
        a1 = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        a2 = np.array([[5.0, 1.0], [2.0, 6.0]], dtype=np.float32)
        a3 = np.array([[3.0, 3.0], [3.0, 3.0]], dtype=np.float32)
        result = VegetationIndexProcessor.create_temporal_composite([a1, a2, a3], method='median')
        expected = np.median([a1, a2, a3], axis=0)
        assert np.allclose(result, expected)

    def test_temporal_composite_mean(self):
        """Temporal composite using mean across scenes."""
        a1 = np.full((5, 5), 0.1, dtype=np.float32)
        a2 = np.full((5, 5), 0.3, dtype=np.float32)
        result = VegetationIndexProcessor.create_temporal_composite([a1, a2], method='mean')
        assert np.allclose(result, 0.2)

    def test_temporal_composite_single_scene(self):
        """Single scene returned as-is."""
        a1 = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        result = VegetationIndexProcessor.create_temporal_composite([a1], method='median')
        assert np.array_equal(result, a1)

    # ---- NDRE Tests ----

    def test_ndre_formula(self):
        """NDRE = (NIR-RedEdge)/(NIR+RedEdge)"""
        nir = np.full((5, 5), 0.5, dtype=np.float32)
        rededge = np.full((5, 5), 0.1, dtype=np.float32)
        p = self._setup_processor({'B8A': rededge, 'B08': nir})
        ndre = p.calculate_ndre(apply_cloud_mask=False)
        expected = 0.4 / 0.6
        assert np.allclose(ndre, expected, atol=1e-4)
