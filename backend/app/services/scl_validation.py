"""
Scene Classification Layer (SCL) validation for Phase 3 micro filter.

Uses rasterio.mask with the exact parcel polygon (not bbox) so the cloud percentage
is computed only over the client's parcel, not over neighboring areas.
Corrupt: 1 (saturated), 3 (cloud shadows), 8 (cloud med), 9 (cloud high), 10 (cirrus).
"""

import logging
from typing import Any, Dict

import numpy as np
import rasterio
from rasterio.mask import mask as rasterio_mask
from rasterio.warp import transform_geom

logger = logging.getLogger(__name__)

# SCL classes to count as corrupt (lethal for NDVI or invalid)
SCL_CORRUPT = (1, 3, 8, 9, 10)


def compute_local_cloud_pct(
    scl_path: str,
    geojson_polygon: Dict[str, Any],
    *,
    corrupt_classes: tuple = SCL_CORRUPT,
) -> float:
    """
    Compute percentage of corrupt pixels inside the parcel using exact geometry.

    Uses rasterio.mask to clip the SCL raster to the parcel polygon (vector mask).
    Only pixels inside the parcel are counted; the bbox is not used, so neighbor
    clouds do not affect the result.

    Args:
        scl_path: Path to local SCL GeoTIFF (single band, integer SCL values).
        geojson_polygon: GeoJSON geometry (Polygon or MultiPolygon) in WGS84 (EPSG:4326).
        corrupt_classes: SCL values to count as corrupt; default (1, 3, 8, 9, 10).

    Returns:
        Percentage 0–100 of corrupt pixels within the parcel only.
    """
    with rasterio.open(scl_path) as src:
        # Geometry from GeoJSON is in WGS84; raster is usually projected (e.g. UTM).
        geom_wgs84 = geojson_polygon
        if src.crs and str(src.crs) != "EPSG:4326":
            geom_proj = transform_geom("EPSG:4326", src.crs, geom_wgs84)
        else:
            geom_proj = geom_wgs84
        # mask() expects a list of shapes; MultiPolygon stays as one feature.
        shapes = [geom_proj]
        try:
            out_image, _ = rasterio_mask(src, shapes, crop=True, nodata=0, filled=True)
        except Exception as e:
            logger.warning("rasterio.mask failed for parcel geometry: %s", e)
            return 100.0
        scl_matrix = out_image[0]
    # Pixels outside the polygon are filled with nodata=0; only >0 are inside the parcel.
    valid_parcel_pixels = int(np.sum(scl_matrix > 0))
    if valid_parcel_pixels == 0:
        return 100.0
    corrupt_pixels = int(np.sum(np.isin(scl_matrix, list(corrupt_classes))))
    return (corrupt_pixels / valid_parcel_pixels) * 100.0
