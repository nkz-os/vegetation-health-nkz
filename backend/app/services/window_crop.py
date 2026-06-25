"""Window-crop raw Sentinel-2 bands to a parcel AOI before super-resolution.

Each band is cropped to the parcel bbox (+ buffer) in the band's OWN CRS,
preserving the windowed affine transform so the downstream index, geometry
mask and COG stay spatially aligned. Runs per-parcel in the calc task,
replacing full-tile Sen2Res with a small windowed crop (orders of magnitude
less CPU/RAM/time).
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Tuple

import rasterio
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as window_transform
from pyproj import Transformer
from shapely.geometry import shape

logger = logging.getLogger(__name__)


def _bounds_4326(parcel_bounds_geojson: dict) -> Tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) of a GeoJSON geometry in EPSG:4326."""
    return shape(parcel_bounds_geojson).bounds


def crop_bands_to_window(
    band_paths: Dict[str, str],
    parcel_bounds_geojson: dict,
    buffer_m: float,
    output_dir: str,
) -> Dict[str, str]:
    """Crop each band to the parcel window (bbox + buffer) in the band CRS.

    Returns ``{band: windowed_geotiff_path}``. Raises ``ValueError`` if the
    window is empty (parcel outside the band footprint) or zero-size.
    """
    minx, miny, maxx, maxy = _bounds_4326(parcel_bounds_geojson)
    os.makedirs(output_dir, exist_ok=True)
    out: Dict[str, str] = {}

    for band, path in band_paths.items():
        with rasterio.open(path) as src:
            crs = src.crs
            if crs and str(crs) != "EPSG:4326":
                tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
                xs, ys = tr.transform(
                    [minx, minx, maxx, maxx], [miny, maxy, miny, maxy]
                )
                pminx, pmaxx = min(xs), max(xs)
                pminy, pmaxy = min(ys), max(ys)
            else:
                pminx, pminy, pmaxx, pmaxy = minx, miny, maxx, maxy

            # Buffer outward (band CRS is metric UTM).
            pminx -= buffer_m
            pminy -= buffer_m
            pmaxx += buffer_m
            pmaxy += buffer_m

            try:
                win = from_bounds(pminx, pminy, pmaxx, pmaxy, src.transform)
                win = win.intersection(Window(0, 0, src.width, src.height))
            except rasterio.errors.WindowError as exc:
                raise ValueError(
                    f"Empty window for band {band}: parcel outside band footprint"
                ) from exc
            win = win.round_offsets().round_lengths()
            if win.width <= 0 or win.height <= 0:
                raise ValueError(
                    f"Empty window for band {band}: parcel outside band footprint"
                )

            data = src.read(1, window=win)
            win_tr = window_transform(win, src.transform)
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                height=int(win.height),
                width=int(win.width),
                transform=win_tr,
                count=1,
            )

        out_path = os.path.join(output_dir, f"{band}_win.tif")
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(data, 1)
        out[band] = out_path
        logger.info(
            "Windowed band %s -> %dx%d px", band, int(win.width), int(win.height)
        )

    return out
