"""
Sen2Res-style super-resolution using content-color factorization.

Super-resolves 20 m Sentinel-2 bands to 10 m using native 10 m bands as
spatial guides.  Runs ONCE per scene at download time, before upload to the
global cache.  Algorithm: tiled linear regression with residual injection
(ATPRK-like), implemented in pure numpy/scipy — zero new dependencies.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from scipy.ndimage import zoom

logger = logging.getLogger(__name__)

# Sentinel-2 native 10 m bands used as spatial guides (RGB + NIR).
GUIDE_BANDS: Tuple[str, ...] = ("B02", "B03", "B04", "B08")

# 20 m bands that can be super-resolved to 10 m.
TARGET_BANDS: Tuple[str, ...] = ("B05", "B8A", "B11", "B12")

# Tile size at 20 m resolution for local regression.
# 256  -> ~22×22 tiles on a full S2 granule; robust local models.
# 0    -> global regression (single model for whole scene).
DEFAULT_TILE_SIZE: int = 256


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def superresolve_bands(
    band_paths: Dict[str, str],
    output_dir: str,
    guide_bands: Sequence[str] = GUIDE_BANDS,
    target_bands: Sequence[str] = TARGET_BANDS,
    tile_size: int = DEFAULT_TILE_SIZE,
    scl_path: Optional[str] = None,
) -> Dict[str, str]:
    """Super-resolve 20 m bands to 10 m and write results to *output_dir*.

    Parameters
    ----------
    band_paths:
        ``{band_name: local_geotiff_path}`` for every downloaded band.
    output_dir:
        Directory where super-resolved GeoTIFFs are written (typically the
        same temp dir the original 20 m files live in).
    guide_bands:
        10 m bands to use as spatial predictors (default B02/B03/B04/B08).
    target_bands:
        20 m bands to super-resolve (default B05/B8A/B11/B12).
    tile_size:
        Regression tile size at 20 m resolution.  0 = global model.
    scl_path:
        Optional path to the SCL GeoTIFF for cloud masking.

    Returns
    -------
    Updated *band_paths* dict.  Target band entries now point to the
    super-resolved 10 m GeoTIFFs; all other entries are unchanged.
    """
    # --- identify which target bands we can actually process ---
    available_targets = [b for b in target_bands if b in band_paths]
    available_guides = [b for b in guide_bands if b in band_paths]

    if not available_targets:
        logger.info("No target bands available — skipping super-resolution")
        return band_paths
    if len(available_guides) < 2:
        logger.warning(
            "Need at least 2 guide bands for super-resolution (got %d) — skipping",
            len(available_guides),
        )
        return band_paths

    logger.info(
        "Super-resolving %d bands → 10 m (guides: %s, tile_size=%d)",
        len(available_targets),
        ",".join(available_guides),
        tile_size,
    )

    # --- load guide bands once (reused across all targets) ---
    guides: Dict[str, np.ndarray] = {}
    guide_meta: dict = {}
    for band in available_guides:
        path = band_paths[band]
        data, meta = _read_band(path)
        guides[band] = data
        if not guide_meta:
            guide_meta = meta
            _validate_10m_resolution(meta, band, path)

    guide_list = [guides[b] for b in available_guides]

    # --- load SCL mask (optional) ---
    scl_mask_20m: Optional[np.ndarray] = None
    if scl_path and os.path.exists(scl_path):
        scl_data, _ = _read_band(scl_path)
        scl_mask_20m = _build_scl_mask(scl_data)

    # --- process each target band ---
    for band in available_targets:
        logger.info("Super-resolving %s …", band)
        target_path = band_paths[band]
        target_20m, target_meta = _read_band(target_path)

        out = _tiled_content_color_factorization(
            target_20m=target_20m,
            guides_10m=guide_list,
            tile_size=tile_size,
            valid_mask_20m=scl_mask_20m,
        )

        # Write SR result, replacing the original 20 m file
        ref_band = available_guides[0]
        ref_path = band_paths[ref_band]
        _write_sr_geotiff(
            data=out.astype(np.float32),
            reference_10m_path=ref_path,
            output_path=target_path,
        )
        logger.info("%s → 10 m (%dx%d)", band, out.shape[1], out.shape[0])

    # band_paths keys are unchanged; target values now point to 10 m files
    return band_paths


# ---------------------------------------------------------------------------
# Algorithm core
# ---------------------------------------------------------------------------


def _tiled_content_color_factorization(
    target_20m: np.ndarray,
    guides_10m: List[np.ndarray],
    tile_size: int = DEFAULT_TILE_SIZE,
    valid_mask_20m: Optional[np.ndarray] = None,
) -> np.ndarray:
    """ATPRK-like super-resolution via tiled linear regression.

    1. Learn ``target = Σ w_k · guide_k + bias`` at 20 m within each tile.
    2. Predict at 10 m using the 10 m guides.
    3. Inject the bilinear-upsampled residual for spectral fidelity.
    """
    H, W = target_20m.shape
    target_shape = (H * 2, W * 2)

    # Down-sample guides to 20 m (2×2 block mean)
    guides_20m = [_downsample_10m_to_20m(g) for g in guides_10m]

    # If no tiling, use the whole image as one tile
    if tile_size <= 0 or tile_size >= max(H, W):
        y_fine = _global_regression_predict(
            target_20m, guides_20m, guides_10m, target_shape, valid_mask_20m
        )
    else:
        y_fine = _tiled_regression_predict(
            target_20m,
            guides_20m,
            guides_10m,
            target_shape,
            tile_size,
            valid_mask_20m,
        )

    # --- residual injection ---
    # Ensures the 20 m block-mean of the output equals the original 20 m data.
    y_fine_20m = _downsample_10m_to_20m(y_fine)
    residual_20m = target_20m - y_fine_20m
    residual_10m = zoom(residual_20m, 2.0, order=1, mode="reflect")

    result = y_fine + residual_10m
    return result.astype(np.float32)


def _global_regression_predict(
    target_20m: np.ndarray,
    guides_20m: List[np.ndarray],
    guides_10m: List[np.ndarray],
    target_shape: Tuple[int, int],
    valid_mask_20m: Optional[np.ndarray],
) -> np.ndarray:
    """Single global linear model."""
    K = len(guides_20m)
    N = target_20m.size

    # Build design matrix at 20 m
    X_20m = np.empty((N, K + 1), dtype=np.float32)
    for k in range(K):
        X_20m[:, k] = guides_20m[k].ravel()
    X_20m[:, K] = 1.0  # intercept

    y = target_20m.ravel()

    if valid_mask_20m is not None:
        keep = valid_mask_20m.ravel()
        X_20m = X_20m[keep, :]
        y = y[keep]

    theta, _res, _rank, _sv = np.linalg.lstsq(X_20m, y, rcond=None)

    # Predict at 10 m
    N_fine = target_shape[0] * target_shape[1]
    X_10m = np.empty((N_fine, K + 1), dtype=np.float32)
    for k in range(K):
        X_10m[:, k] = guides_10m[k].ravel()
    X_10m[:, K] = 1.0

    y_fine = (X_10m @ theta).reshape(target_shape)
    return y_fine


def _tiled_regression_predict(
    target_20m: np.ndarray,
    guides_20m: List[np.ndarray],
    guides_10m: List[np.ndarray],
    target_shape: Tuple[int, int],
    tile_size: int,
    valid_mask_20m: Optional[np.ndarray],
) -> np.ndarray:
    """Tile the 20 m space, fit one linear model per tile, predict at 10 m.

    Uses linear blending in a narrow border (tile_size // 8 px) to avoid
    seams at tile boundaries.
    """
    H, W = target_20m.shape
    K = len(guides_20m)
    blend = max(tile_size // 8, 1)

    y_fine = np.zeros(target_shape, dtype=np.float32)
    weight = np.zeros(target_shape, dtype=np.float32)

    for i0 in range(0, H, tile_size):
        i1 = min(i0 + tile_size, H)
        for j0 in range(0, W, tile_size):
            j1 = min(j0 + tile_size, W)

            # --- extract patches at 20 m ---
            t_patch = target_20m[i0:i1, j0:j1]
            ph, pw = t_patch.shape
            g20_patches = [g[i0:i1, j0:j1] for g in guides_20m]

            # Build design matrix
            X = np.empty((ph * pw, K + 1), dtype=np.float32)
            for k in range(K):
                X[:, k] = g20_patches[k].ravel()
            X[:, K] = 1.0

            y = t_patch.ravel()

            if valid_mask_20m is not None:
                vm = valid_mask_20m[i0:i1, j0:j1].ravel()
                X = X[vm, :]
                y = y[vm]

            if X.shape[0] < K + 2:
                continue  # not enough valid pixels

            theta, _res, _rank, _sv = np.linalg.lstsq(X, y, rcond=None)

            # --- predict at 10 m ---
            fi0, fi1 = i0 * 2, i1 * 2
            fj0, fj1 = j0 * 2, j1 * 2
            fph, fpw = fi1 - fi0, fj1 - fj0

            X_fine = np.empty((fph * fpw, K + 1), dtype=np.float32)
            for k in range(K):
                g10_patch = guides_10m[k][fi0:fi1, fj0:fj1]
                X_fine[:, k] = g10_patch.ravel()
            X_fine[:, K] = 1.0

            pred = (X_fine @ theta).reshape(fph, fpw)

            # --- linear blending weights (ramp near borders) ---
            wy = np.ones(fph, dtype=np.float32)
            wx = np.ones(fpw, dtype=np.float32)
            b = blend * 2  # blend zone at 10 m = 2× the 20 m blend zone
            if i0 > 0:
                wy[:b] = np.linspace(0, 1, b)
            if i1 < H:
                wy[-b:] = np.linspace(1, 0, b)
            if j0 > 0:
                wx[:b] = np.linspace(0, 1, b)
            if j1 < W:
                wx[-b:] = np.linspace(1, 0, b)
            w = np.outer(wy, wx)

            y_fine[fi0:fi1, fj0:fj1] += pred * w
            weight[fi0:fi1, fj0:fj1] += w

    # Normalise
    valid = weight > 0
    y_fine[valid] /= weight[valid]

    # Fill any gaps (shouldn't happen with proper tiling) via bilinear
    if not np.all(valid):
        fill = zoom(target_20m, 2.0, order=1)
        y_fine[~valid] = fill[~valid]

    return y_fine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _downsample_10m_to_20m(band_10m: np.ndarray) -> np.ndarray:
    """Average-pool a 10 m band to 20 m (2×2 block mean)."""
    H, W = band_10m.shape
    H2, W2 = H // 2, W // 2
    # Reshape and mean over 2×2 blocks — discards trailing row/col if odd
    return band_10m[: H2 * 2, : W2 * 2].reshape(H2, 2, W2, 2).mean(axis=(1, 3))


def _read_band(path: str) -> Tuple[np.ndarray, dict]:
    """Read single-band GeoTIFF, return (float32 array, meta dict)."""
    with rasterio.open(path) as src:
        data = src.read(1).astype(np.float32)
        meta = {
            "crs": src.crs,
            "transform": src.transform,
            "width": src.width,
            "height": src.height,
        }
    return data, meta


def _validate_10m_resolution(meta: dict, band: str, path: str) -> None:
    """Warn if a guide band does not look like 10 m resolution."""
    res_x = abs(meta["transform"].a)
    res_y = abs(meta["transform"].e)
    if res_x > 15 or res_y > 15:
        logger.warning(
            "Guide band %s (%s) resolution is %.1f×%.1f m — expected ~10 m",
            band,
            path,
            res_x,
            res_y,
        )


def _build_scl_mask(scl_data: np.ndarray) -> np.ndarray:
    """Build boolean validity mask from SCL band at 20 m.

    True  = pixel is valid for regression (vegetation / bare soil / water).
    False = cloud, shadow, snow, no-data — excluded from model fitting.
    """
    VALID_SCL = {4, 5, 6, 7}  # vegetation, bare soil, water, unclassified
    mask = np.isin(scl_data, list(VALID_SCL))
    return mask


def _write_sr_geotiff(
    data: np.ndarray,
    reference_10m_path: str,
    output_path: str,
) -> None:
    """Write *data* as a GeoTIFF, copying CRS and 10 m transform from *reference_10m_path*."""
    with rasterio.open(reference_10m_path) as ref:
        profile = ref.profile.copy()
    profile.update(
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        dtype="float32",
        count=1,
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
    )
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(data, 1)
