"""
Scene Classification Layer (SCL) validation for Phase 3 micro filter.

Uses rasterio.mask with the parcel polygon plus a 10m outward buffer so
Sentinel-2 edge pixels that partially overlap the boundary are included.
Corrupt: 1 (saturated), 3 (cloud shadows), 8 (cloud med), 9 (cloud high), 10 (cirrus).
"""

import logging
from typing import Any, Dict

import numpy as np
import rasterio
from rasterio.mask import mask as rasterio_mask
from shapely.geometry import shape as shp
from shapely.ops import transform
from pyproj import Transformer

logger = logging.getLogger(__name__)

# SCL classes to count as corrupt (lethal for NDVI or invalid)
SCL_CORRUPT = (1, 3, 8, 9, 10)

# Buffer (meters) applied to parcel geometry so Sentinel-2 edge pixels that
# partially overlap the boundary are included in the cloud check.
PARCEL_GEOMETRY_BUFFER_M = 10.0


def compute_local_cloud_pct(
    scl_path: str,
    geojson_polygon: Dict[str, Any],
    *,
    corrupt_classes: tuple = SCL_CORRUPT,
) -> float:
    """
    Compute percentage of corrupt pixels inside the parcel using buffered geometry.

    The parcel polygon is buffered outward by PARCEL_GEOMETRY_BUFFER_M to include
    Sentinel-2 pixels (10m) whose center falls just outside the boundary but whose
    footprint still overlaps the parcel interior.

    Args:
        scl_path: Path to local SCL GeoTIFF (single band, integer SCL values).
        geojson_polygon: GeoJSON geometry (Polygon or MultiPolygon) in WGS84 (EPSG:4326).
        corrupt_classes: SCL values to count as corrupt; default (1, 3, 8, 9, 10).

    Returns:
        Percentage 0–100 of corrupt pixels within the buffered parcel area.
    """
    with rasterio.open(scl_path) as src:
        geom = shp(geojson_polygon)
        if src.crs and str(src.crs) != "EPSG:4326":
            transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            geom = transform(transformer.transform, geom)
        geom = geom.buffer(PARCEL_GEOMETRY_BUFFER_M)
        shapes = [geom.__geo_interface__]
        try:
            out_image, _ = rasterio_mask(src, shapes, crop=True, nodata=0, filled=True)
        except Exception as e:
            logger.warning("rasterio.mask failed for parcel geometry: %s", e)
            return 100.0
        scl_matrix = out_image[0]
    valid_parcel_pixels = int(np.sum(scl_matrix > 0))
    if valid_parcel_pixels == 0:
        return 100.0
    corrupt_pixels = int(np.sum(np.isin(scl_matrix, list(corrupt_classes))))
    return (corrupt_pixels / valid_parcel_pixels) * 100.0
