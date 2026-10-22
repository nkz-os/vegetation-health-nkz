"""CLMS LST processor — Copernicus Land Monitoring Service LST (COG format).

Queries the CDSE STAC catalog for ``clms_lst_global_3km_hourly_v3_cog``
(hourly NRT LST at ~3 km, EPSG:4326, from geostationary satellites),
downloads the COG tile covering the parcel, extracts zonal mean LST in °C,
and feeds the same ``upsert_eo_lst`` path as Landsat TIRS.

Unlike raw Sentinel-3 SLSTR (NetCDF swath), CLMS LST is already:
- Reprojected to a regular geographic grid (EPSG:4326)
- In COG (Cloud-Optimized GeoTIFF) format
- Atmosphere-corrected and quality-flagged
- Available hourly (NRT, ~4h latency)

Resolution is ~3 km — suitable for zonal monitoring of medium-to-large
parcels and as a frequent temporal bridge between Landsat acquisitions.
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
CLMS_LST_COLLECTION = "clms_lst_global_3km_hourly_v3_cog"

# CLMS LST stores values in Kelvin, scaled and offset.
# V3 uses: LST(K) = DN * 0.02 (no offset needed per product metadata).
KELVIN_TO_CELSIUS = 273.15
LST_SCALE = 0.02
PARCEL_BUFFER_M = 10.0


def _kelvin_to_celsius(dn: np.ndarray) -> np.ndarray:
    """Convert CLMS LST DN to Celsius (K * scale − 273.15)."""
    arr = dn.astype(np.float64)
    kelvin = arr * LST_SCALE
    celsius = kelvin - KELVIN_TO_CELSIUS
    celsius[arr <= 0] = np.nan
    return celsius


def search_latest_clms_lst(
    latitude: float,
    longitude: float,
    *,
    window_hours: int = 24,
    auth_headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return the most recent CLMS hourly LST feature covering a point."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    payload: dict = {
        "collections": [CLMS_LST_COLLECTION],
        "intersects": {"type": "Point", "coordinates": [longitude, latitude]},
        "datetime": f"{start}/{end}",
        "limit": 1,
    }
    headers = dict(auth_headers or {})
    headers.setdefault("Content-Type", "application/json")
    # CDSE STAC is public — no auth needed for search
    resp = requests.post(
        f"{CDSE_STAC_URL}/search", json=payload, headers=headers, timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("CDSE CLMS LST STAC returned %d", resp.status_code)
        return None
    features = resp.json().get("features", [])
    return features[0] if features else None


def compute_clms_lst_zonal(
    raster_path: str,
    geometry_geojson: dict,
) -> dict[str, float | int]:
    """Compute zonal LST (°C) from a CLMS COG tile over parcel geometry."""
    geom = shp_shape(geometry_geojson)
    with rasterio.open(raster_path) as src:
        if src.crs and str(src.crs) != "EPSG:4326":
            from pyproj import Transformer
            transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            geom = shp_transform(transformer.transform, geom)
        geom = geom.buffer(PARCEL_BUFFER_M)
        try:
            out_image, _ = rio_mask(
                src, [geom.__geo_interface__], crop=True, nodata=0,
            )
        except ValueError:
            return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0, "pixel_count": 0}
        celsius = _kelvin_to_celsius(out_image[0])
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


def process_clms_lst(
    latitude: float,
    longitude: float,
    geometry_geojson: dict,
    *,
    auth_headers: dict[str, str] | None = None,
    window_hours: int = 24,
) -> tuple[dict | None, str | None, str | None]:
    """End-to-end CLMS LST extraction for one parcel.

    Returns:
        (statistics, sensing_datetime ISO8601, scene_id) or (None, None, None).
    """
    feature = search_latest_clms_lst(
        latitude, longitude, window_hours=window_hours, auth_headers=auth_headers,
    )
    if not feature:
        return None, None, None

    assets = feature.get("assets", {})
    lst_asset = assets.get("LST") or assets.get("data") or list(assets.values())[0] if assets else None
    if not lst_asset or not lst_asset.get("href"):
        logger.warning("CLMS LST scene %s has no data asset", feature.get("id"))
        return None, None, None

    scene_id = feature.get("id", "unknown")
    sensing_dt = feature.get("properties", {}).get("datetime", "")
    href = lst_asset["href"]

    with tempfile.TemporaryDirectory() as tmpdir:
        tif_path = os.path.join(tmpdir, "clms_lst.tif")
        try:
            headers = dict(auth_headers or {})
            with requests.get(href, headers=headers, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(tif_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            fh.write(chunk)
        except Exception as exc:
            logger.warning("CLMS LST download failed for %s: %s", scene_id, exc)
            return None, None, None
        stats = compute_clms_lst_zonal(tif_path, geometry_geojson)
        if stats["pixel_count"] == 0:
            return None, None, None
        return stats, sensing_dt or None, scene_id
