"""SAR Soil Moisture Index (SMI) processor.

Normalises Sentinel-1 GRD backscatter by local incidence angle (DEM-derived)
to produce a physically-grounded 0–1 soil moisture proxy. Uses Horn (1981)
gradient from nkz-platform-sdk for terrain correction.

Reference:
- Attema & Ulaby (1978) — bare-soil σ₀ ∝ soil moisture
- Horn (1981) — finite-difference gradient
- ESA SNAP Radiometric Terrain Correction (RTC)
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from typing import Any

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from shapely.geometry import shape as shp_shape
from shapely.ops import transform as shp_transform

try:
    from nkz_platform_sdk.gis.terrain import slope_degrees, aspect_degrees
except ImportError:  # pragma: no cover — fallback stub until SDK≥0.6.1
    def slope_degrees(elev, pixel_size): return np.zeros_like(elev)  # noqa: ARG001
    def aspect_degrees(elev, pixel_size): return np.zeros_like(elev)  # noqa: ARG001

logger = logging.getLogger(__name__)

# Typical VV backscatter range (dB) for agricultural bare soil
# — after incidence-angle normalisation, the residual is mostly moisture.
VV_DRY_DB = -15.0   # very dry soil
VV_WET_DB = -5.0     # saturated soil
DEFAULT_INCIDENCE_DEG = 38.0  # Sentinel-1 IW typical


def _angle_of_incidence(
    orbit_direction: str,       # ASCENDING or DESCENDING
    nominal_incidence_deg: float,
    slope_deg: Any,
    aspect_deg: Any,
) -> Any:
    """Compute local incidence angle (radians) accounting for terrain.

    θ_local = θ_nom - slope · cos(aspect - satellite_azimuth)

    For Sentinel-1:
    - Ascending  → right-looking east  (azimuth ~190°)
    - Descending → right-looking west  (azimuth ~350°)
    """
    sat_azimuth_deg = 190.0 if orbit_direction.upper().startswith("ASC") else 350.0
    nominal_rad = math.radians(nominal_incidence_deg)
    slope_rad = np.radians(np.asarray(slope_deg, dtype=float))
    aspect_rad = np.radians(np.asarray(aspect_deg, dtype=float))
    sat_az_rad = math.radians(sat_azimuth_deg)

    cos_correction = np.cos(aspect_rad - sat_az_rad)
    local_rad = nominal_rad - slope_rad * cos_correction
    # Clamp to physically plausible range (1° – 85°)
    return np.clip(local_rad, math.radians(1.0), math.radians(85.0))


def _normalise_vv(
    vv_db: np.ndarray,
    incidence_local_rad: np.ndarray,
    incidence_nominal_deg: float,
) -> np.ndarray:
    """Incidence-angle normalisation: σ₀_corr = σ₀_raw + 10·log₁₀(cosθ_nom/cosθ_local)."""
    cos_nom = math.cos(math.radians(incidence_nominal_deg))
    cos_local = np.cos(incidence_local_rad)
    cos_local = np.where(cos_local > 0.001, cos_local, 0.001)
    correction = 10.0 * np.log10(cos_nom / cos_local)
    return vv_db + correction


def _vv_to_smi(vv_normalised: np.ndarray) -> np.ndarray:
    """Map normalised VV backscatter (dB) to 0–1 soil moisture index."""
    smi = (vv_normalised - VV_DRY_DB) / (VV_WET_DB - VV_DRY_DB)
    return np.clip(smi, 0.0, 1.0)


def compute_smi(
    vv_path: str,
    geometry_geojson: dict,
    *,
    dem_path: str | None = None,
    elevation_array: np.ndarray | None = None,
    nominal_incidence_deg: float = DEFAULT_INCIDENCE_DEG,
    orbit_direction: str = "ASCENDING",
) -> dict[str, float]:
    """Compute zonal SMI (0–1) for a parcel from Sentinel-1 VV GeoTIFF.

    Args:
        vv_path: Path to VV polarisation GeoTIFF.
        geometry_geojson: Parcel geometry (EPSG:4326 GeoJSON).
        dem_path: Optional DEM GeoTIFF for terrain correction.
        elevation_array: Optional 2-D elevation array (alternative to dem_path).
        nominal_incidence_deg: Nominal incidence angle from S1 metadata.
        orbit_direction: ASCENDING or DESCENDING.

    Returns:
        Dict with mean, min, max, std, pixel_count for the SMI raster.
    """
    geom = shp_shape(geometry_geojson)
    with rasterio.open(vv_path) as src:
        raster_crs = src.crs
        pixel_size_deg = abs(src.transform.a)  # approximate decimal degrees

        # Crop to parcel
        if raster_crs and str(raster_crs) != "EPSG:4326":
            from pyproj import Transformer
            transformer = Transformer.from_crs("EPSG:4326", raster_crs, always_xy=True)
            geom = shp_transform(transformer.transform, geom)

        geom_buf = geom.buffer(10.0)  # 10 m buffer
        out_image, out_transform = rio_mask(
            src, [geom_buf.__geo_interface__], crop=True, nodata=0,
        )
        vv = out_image[0].astype(np.float64)
        valid = (vv != 0) & np.isfinite(vv)
        if not np.any(valid):
            return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0, "pixel_count": 0}

        # ── Terrain correction ───────────────────────────────────────────
        rows, cols = vv.shape
        if dem_path or elevation_array is not None:
            if dem_path:
                with rasterio.open(dem_path) as dem_src:
                    dem_img, _ = rio_mask(
                        dem_src, [geom_buf.__geo_interface__], crop=True, nodata=-9999,
                    )
                    elev = dem_img[0].astype(np.float64)
            else:
                elev = np.asarray(elevation_array, dtype=np.float64)
            if elev.shape != (rows, cols):
                elev = np.full((rows, cols), 100.0)  # fallback
            slope = slope_degrees(elev, pixel_size_deg)
            aspect = aspect_degrees(elev, pixel_size_deg)
        else:
            slope = np.zeros((rows, cols))
            aspect = np.zeros((rows, cols))

        local_rad = _angle_of_incidence(
            orbit_direction, nominal_incidence_deg, slope, aspect,
        )
        vv_corrected = _normalise_vv(vv, local_rad, nominal_incidence_deg)

    # ── SMI conversion ───────────────────────────────────────────────────
    smi = _vv_to_smi(vv_corrected)
    smi_valid = smi[valid & np.isfinite(smi)]
    if smi_valid.size == 0:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0, "pixel_count": 0}

    return {
        "mean": float(np.mean(smi_valid)),
        "min": float(np.min(smi_valid)),
        "max": float(np.max(smi_valid)),
        "std": float(np.std(smi_valid)),
        "pixel_count": int(smi_valid.size),
    }
