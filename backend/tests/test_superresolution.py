"""Tests for superresolution module — synthetic arrays only (no real S2 data)."""

import importlib.util
import os
import sys
import tempfile

import numpy as np
import pytest

# Import superresolution.py directly, bypassing app.services.__init__ which
# pulls in fastapi/redis/geoalchemy2 and all their transitive deps.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "services", "superresolution.py"
)
_spec = importlib.util.spec_from_file_location(
    "superresolution", _MODULE_PATH
)
_sr = importlib.util.module_from_spec(_spec)
sys.modules["superresolution"] = _sr
_spec.loader.exec_module(_sr)

# Now rasterio (imported by superresolution) is available; grab local names
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

_build_scl_mask = _sr._build_scl_mask
_downsample_10m_to_20m = _sr._downsample_10m_to_20m
_global_regression_predict = _sr._global_regression_predict
_tiled_content_color_factorization = _sr._tiled_content_color_factorization
_tiled_regression_predict = _sr._tiled_regression_predict
superresolve_bands = _sr.superresolve_bands


class TestDownsample:
    def test_factor_two(self) -> None:
        data = np.arange(64, dtype=np.float32).reshape(8, 8)
        result = _downsample_10m_to_20m(data)
        assert result.shape == (4, 4)

    def test_block_mean_exact(self) -> None:
        data = np.array([[0, 2], [4, 6]], dtype=np.float32)
        result = _downsample_10m_to_20m(data)
        assert result.shape == (1, 1)
        assert np.isclose(result[0, 0], 3.0)

    def test_odd_dimensions_discarded(self) -> None:
        data = np.arange(25, dtype=np.float32).reshape(5, 5)
        result = _downsample_10m_to_20m(data)
        assert result.shape == (2, 2)


class TestGlobalRegression:
    def test_output_shape(self) -> None:
        rng = np.random.RandomState(42)
        target_20m = rng.rand(32, 32).astype(np.float32)
        guides_10m = [rng.rand(64, 64).astype(np.float32) for _ in range(4)]
        guides_20m = [_downsample_10m_to_20m(g) for g in guides_10m]
        out = _global_regression_predict(
            target_20m, guides_20m, guides_10m, (64, 64), None
        )
        assert out.shape == (64, 64)

    def test_perfect_linear_recovery(self) -> None:
        """If target == linear combo of guides, SR recovers exactly."""
        rng = np.random.RandomState(123)
        guides_10m = [rng.rand(64, 64).astype(np.float32) for _ in range(4)]
        guides_20m = [_downsample_10m_to_20m(g) for g in guides_10m]

        true_target_10m = (
            0.2 * guides_10m[0]
            + 0.4 * guides_10m[1]
            + 0.1 * guides_10m[2]
            + 0.3 * guides_10m[3]
        )
        target_20m = _downsample_10m_to_20m(true_target_10m)

        out = _global_regression_predict(
            target_20m, guides_20m, guides_10m, (64, 64), None
        )
        # Without residual injection we expect small numerical error
        assert np.allclose(out, true_target_10m, atol=1e-4)


class TestTiledRegression:
    def test_output_shape(self) -> None:
        rng = np.random.RandomState(42)
        target_20m = rng.rand(64, 64).astype(np.float32)
        guides_10m = [rng.rand(128, 128).astype(np.float32) for _ in range(4)]
        guides_20m = [_downsample_10m_to_20m(g) for g in guides_10m]
        out = _tiled_regression_predict(
            target_20m, guides_20m, guides_10m, (128, 128), tile_size=32, valid_mask_20m=None
        )
        assert out.shape == (128, 128)

    def test_no_nan_output(self) -> None:
        rng = np.random.RandomState(77)
        target_20m = rng.rand(60, 60).astype(np.float32)
        guides_10m = [rng.rand(120, 120).astype(np.float32) for _ in range(4)]
        guides_20m = [_downsample_10m_to_20m(g) for g in guides_10m]
        out = _tiled_regression_predict(
            target_20m, guides_20m, guides_10m, (120, 120), tile_size=20, valid_mask_20m=None
        )
        assert not np.any(np.isnan(out))


class TestContentColorFactorization:
    def test_residual_injection_preserves_coarse_mean(self) -> None:
        """Down-sampled SR output should closely match the original 20 m data."""
        rng = np.random.RandomState(99)
        # Create correlated guides and target
        guides_10m = [rng.rand(64, 64).astype(np.float32) for _ in range(4)]
        true_10m = (
            0.3 * guides_10m[0] + 0.5 * guides_10m[1] + rng.randn(64, 64).astype(np.float32) * 0.02
        )
        target_20m = _downsample_10m_to_20m(true_10m)

        out = _tiled_content_color_factorization(
            target_20m, guides_10m, tile_size=32
        )
        out_20m = _downsample_10m_to_20m(out)
        # Residual injection keeps the 20 m grid mean close to original
        assert np.allclose(out_20m, target_20m, atol=0.05)


class TestSCLMask:
    def test_valid_classes(self) -> None:
        scl = np.array([[4, 5, 1], [6, 7, 3]], dtype=np.uint8)
        mask = _build_scl_mask(scl)
        expected = np.array([[True, True, False], [True, True, False]])
        assert np.array_equal(mask, expected)


class TestSuperresolveBands:
    def test_end_to_end(self) -> None:
        """Full pipeline with synthetic GeoTIFFs."""
        rng = np.random.RandomState(42)

        with tempfile.TemporaryDirectory() as tmpdir:
            band_paths: dict[str, str] = {}
            tr_10m = from_origin(600000, 4800000, 10, -10)
            tr_20m = from_origin(600000, 4800000, 20, -20)

            # Write 10 m guide bands
            for band in ("B02", "B03", "B04", "B08"):
                path = os.path.join(tmpdir, f"{band}.tif")
                with rasterio.open(
                    path, "w", driver="GTiff", height=64, width=64,
                    count=1, dtype="float32", crs="EPSG:32633", transform=tr_10m,
                ) as dst:
                    dst.write(rng.rand(64, 64).astype(np.float32), 1)
                band_paths[band] = path

            # Write 20 m target bands (half the dimensions)
            for band in ("B05", "B8A", "B11", "B12"):
                path = os.path.join(tmpdir, f"{band}.tif")
                with rasterio.open(
                    path, "w", driver="GTiff", height=32, width=32,
                    count=1, dtype="float32", crs="EPSG:32633", transform=tr_20m,
                ) as dst:
                    dst.write(rng.rand(32, 32).astype(np.float32), 1)
                band_paths[band] = path

            result = superresolve_bands(band_paths, tmpdir, tile_size=0)

            # All keys preserved
            for band in ("B02", "B03", "B04", "B08", "B05", "B8A", "B11", "B12"):
                assert band in result

            # Target bands are now 64×64 (10 m), not 32×32
            for band in ("B05", "B8A", "B11", "B12"):
                with rasterio.open(result[band]) as src:
                    assert src.width == 64, f"{band} width"
                    assert src.height == 64, f"{band} height"

    def test_guide_bands_unchanged(self) -> None:
        """10 m guide bands should not be modified."""
        rng = np.random.RandomState(7)

        with tempfile.TemporaryDirectory() as tmpdir:
            band_paths: dict[str, str] = {}
            tr_10m = from_origin(0, 0, 10, -10)
            tr_20m = from_origin(0, 0, 20, -20)

            for band in ("B02", "B04"):
                path = os.path.join(tmpdir, f"{band}.tif")
                with rasterio.open(
                    path, "w", driver="GTiff", height=32, width=32,
                    count=1, dtype="float32", crs="EPSG:32633", transform=tr_10m,
                ) as dst:
                    dst.write(rng.rand(32, 32).astype(np.float32), 1)
                band_paths[band] = path

            for band in ("B05",):
                path = os.path.join(tmpdir, f"{band}.tif")
                with rasterio.open(
                    path, "w", driver="GTiff", height=16, width=16,
                    count=1, dtype="float32", crs="EPSG:32633", transform=tr_20m,
                ) as dst:
                    dst.write(rng.rand(16, 16).astype(np.float32), 1)
                band_paths[band] = path

            result = superresolve_bands(band_paths, tmpdir, tile_size=0)

            # Guide bands: same dimensions
            with rasterio.open(result["B02"]) as src:
                assert src.width == 32
            with rasterio.open(result["B04"]) as src:
                assert src.width == 32
            # Target band: doubled
            with rasterio.open(result["B05"]) as src:
                assert src.width == 32

    def test_missing_targets_no_error(self) -> None:
        """When no target bands are available, just pass through."""
        rng = np.random.RandomState(1)

        with tempfile.TemporaryDirectory() as tmpdir:
            band_paths: dict[str, str] = {}
            tr = from_origin(0, 0, 10, -10)
            for band in ("B02", "B03"):
                path = os.path.join(tmpdir, f"{band}.tif")
                with rasterio.open(
                    path, "w", driver="GTiff", height=32, width=32,
                    count=1, dtype="float32", crs="EPSG:32633", transform=tr,
                ) as dst:
                    dst.write(rng.rand(32, 32).astype(np.float32), 1)
                band_paths[band] = path

            result = superresolve_bands(band_paths, tmpdir)
            assert result == band_paths  # unchanged

    def test_few_guides_no_crash(self) -> None:
        """Single guide band should just skip (need >=2)."""
        rng = np.random.RandomState(2)

        with tempfile.TemporaryDirectory() as tmpdir:
            band_paths: dict[str, str] = {}
            tr_10m = from_origin(0, 0, 10, -10)
            tr_20m = from_origin(0, 0, 20, -20)

            path = os.path.join(tmpdir, "B02.tif")
            with rasterio.open(
                path, "w", driver="GTiff", height=32, width=32,
                count=1, dtype="float32", crs="EPSG:32633", transform=tr_10m,
            ) as dst:
                dst.write(rng.rand(32, 32).astype(np.float32), 1)
            band_paths["B02"] = path

            path = os.path.join(tmpdir, "B05.tif")
            with rasterio.open(
                path, "w", driver="GTiff", height=16, width=16,
                count=1, dtype="float32", crs="EPSG:32633", transform=tr_20m,
            ) as dst:
                dst.write(rng.rand(16, 16).astype(np.float32), 1)
            band_paths["B05"] = path

            result = superresolve_bands(band_paths, tmpdir)
            # Should skip gracefully — B05 unchanged
            assert result == band_paths
