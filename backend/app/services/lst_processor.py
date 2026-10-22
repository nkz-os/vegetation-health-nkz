"""Landsat LST processor — Copernicus CDSE ST_B10 zonal statistics.

Searches Landsat Collection 2 Level-2 Surface Temperature (landsat-c2l2-st),
downloads ST_B10, converts DN to °C (USGS C2 ST scaling), and computes
parcel zonal mean for upsert into EOProduct.lst.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from shapely.geometry import shape as shp_shape
from shapely.ops import transform as shp_transform
import requests

logger = logging.getLogger(__name__)

CDSE_STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac"
LST_COLLECTION = "landsat-c2l2-st"
ST_B10_SCALE = 0.00341802
ST_B10_OFFSET = 149.0
KELVIN_TO_CELSIUS = 273.15
PARCEL_BUFFER_M = 10.0


def _dn_to_celsius(dn: np.ndarray) -> np.ndarray:
    """Convert Landsat C2 L2 ST_B10 DN to Celsius."""
    arr = dn.astype(np.float64)
    kelvin = arr * ST_B10_SCALE + ST_B10_OFFSET
    celsius = kelvin - KELVIN_TO_CELSIUS
    # USGS fill / invalid values
    celsius[(arr <= 0) | (arr >= 65535)] = np.nan
    return celsius


def search_latest_lst_scene(
    latitude: float,
    longitude: float,
    *,
    window_days: int = 32,
    max_cloud_cover: float = 70.0,
    auth_headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return the newest clear Landsat C2L2-ST scene intersecting a point."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=window_days)).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT23:59:59Z")

    payload = {
        "collections": [LST_COLLECTION],
        "intersects": {"type": "Point", "coordinates": [longitude, latitude]},
        "datetime": f"{start}/{end}",
        "limit": 1,
        "query": {
            "platform": {"in": ["landsat-8", "landsat-9"]},
            "eo:cloud_cover": {"lte": max_cloud_cover},
        },
    }
    headers = dict(auth_headers or {})
    headers.setdefault("Content-Type", "application/json")
    resp = requests.post(f"{CDSE_STAC_URL}/search", json=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        logger.warning("CDSE LST STAC search returned %d", resp.status_code)
        return None
    features = resp.json().get("features", [])
    return features[0] if features else None


def _download_asset(href: str, dest: str, auth_headers: dict[str, str] | None) -> None:
    headers = dict(auth_headers or {})
    with requests.get(href, headers=headers, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)


def compute_lst_zonal_stats(
    raster_path: str,
    geometry_geojson: dict,
) -> dict[str, float]:
    """Compute zonal LST statistics (°C) over a parcel geometry."""
    geom = shp_shape(geometry_geojson)
    with rasterio.open(raster_path) as src:
        if src.crs and str(src.crs) != "EPSG:4326":
            from pyproj import Transformer

            transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            geom = shp_transform(transformer.transform, geom)
        geom = geom.buffer(PARCEL_BUFFER_M)
        out_image, _ = rio_mask(src, [geom.__geo_interface__], crop=True, nodata=0)
        celsius = _dn_to_celsius(out_image[0])
        valid = celsius[~np.isnan(celsius)]
        if valid.size == 0:
            return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0, "pixel_count": 0}
        return {
            "mean": float(np.mean(valid)),
            "min": float(np.min(valid)),
            "max": float(np.max(valid)),
            "std": float(np.std(valid)),
            "pixel_count": int(valid.size),
        }


def process_parcel_lst(
    latitude: float,
    longitude: float,
    geometry_geojson: dict,
    *,
    auth_headers: dict[str, str] | None = None,
    window_days: int = 32,
) -> tuple[dict[str, float] | None, str | None, str | None]:
    """End-to-end LST extraction for one parcel.

    Returns:
        (statistics, sensing_date YYYY-MM-DD, scene_id) or (None, None, None).
    """
    feature = search_latest_lst_scene(
        latitude, longitude, window_days=window_days, auth_headers=auth_headers,
    )
    if not feature:
        return None, None, None

    assets = feature.get("assets", {})
    st_b10 = assets.get("ST_B10") or assets.get("st_b10")
    if not st_b10 or not st_b10.get("href"):
        logger.warning("LST scene %s has no ST_B10 asset", feature.get("id"))
        return None, None, None

    scene_id = feature.get("id", "unknown")
    sensing_date = (feature.get("properties", {}).get("datetime") or "")[:10]
    href = st_b10["href"]

    with tempfile.TemporaryDirectory() as tmpdir:
        tif_path = os.path.join(tmpdir, "ST_B10.tif")
        try:
            _download_asset(href, tif_path, auth_headers)
        except Exception as exc:
            logger.warning("LST band download failed for %s: %s", scene_id, exc)
            return None, None, None
        stats = compute_lst_zonal_stats(tif_path, geometry_geojson)
        if stats["pixel_count"] == 0:
            return None, None, None
        return stats, sensing_date or None, scene_id
