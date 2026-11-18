"""Interior statistics of a vegetation index over a parcel.

Pure functions: masks rasterised on the raster grid and statistics over the
index array. The parcel interior (parcel minus ``CORE_BUFFER_M``) avoids the
edge pixels that mix crop with tracks or the neighbouring field.
"""
from typing import Optional, Tuple

import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import shape as shp
from shapely.ops import transform

CORE_BUFFER_M = 10.0
MIN_CORE_PIXELS = 20
# Sentinel-2 SCL: cloud shadows, medium and high probability cloud, thin cirrus.
CLOUD_SCL_CLASSES = frozenset({3, 8, 9, 10})


def _resample_nearest(mask: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resample of a 2-D array to ``shape`` (same extent)."""
    if mask.shape == tuple(shape):
        return mask
    rows = (np.arange(shape[0]) * mask.shape[0]) // shape[0]
    cols = (np.arange(shape[1]) * mask.shape[1]) // shape[1]
    return mask[np.ix_(rows, cols)]


def _projected_parcel(band_meta: dict, parcel_geojson: dict):
    geom = shp(parcel_geojson)
    raster_crs = band_meta.get('crs')
    if raster_crs and str(raster_crs) != 'EPSG:4326':
        transformer = Transformer.from_crs('EPSG:4326', raster_crs, always_xy=True)
        geom = transform(transformer.transform, geom)
    return geom


def _inside(band_meta: dict, geom, shape: Tuple[int, int]) -> np.ndarray:
    """True where a pixel centre lies inside ``geom``, on ``shape``."""
    if geom.is_empty:
        return np.zeros(shape, dtype=bool)
    burned = rasterize(
        [(geom, 1)],
        out_shape=(band_meta['height'], band_meta['width']),
        transform=band_meta['transform'],
        fill=0,
        all_touched=False,
        dtype=np.uint8,
    )
    return _resample_nearest(burned == 1, shape)


def parcel_mask(band_meta: dict, parcel_geojson: dict, shape: Tuple[int, int]) -> np.ndarray:
    """Exact parcel, True = inside, on the index grid ``shape``."""
    return _inside(band_meta, _projected_parcel(band_meta, parcel_geojson), shape)


def interior_mask(band_meta: dict, parcel_geojson: dict,
                  shape: Tuple[int, int]) -> Tuple[np.ndarray, str]:
    """Parcel minus ``CORE_BUFFER_M`` (True = inside) and its name.

    Falls back to the exact parcel (name ``"parcel"``) when the interior keeps
    fewer than ``MIN_CORE_PIXELS`` pixels on the index grid.
    """
    geom = _projected_parcel(band_meta, parcel_geojson)
    core = _inside(band_meta, geom.buffer(-CORE_BUFFER_M), shape)
    if int(core.sum()) >= MIN_CORE_PIXELS:
        return core, "core"
    return _inside(band_meta, geom, shape), "parcel"


def interior_statistics(index_array: np.ndarray, inside: np.ndarray,
                        mask_name: str) -> Optional[dict]:
    """Median, mean, p10, p90 and valid fraction of the index inside the mask.

    None when the mask has no pixels. With no valid pixel, the four statistics
    are None: a value is never invented.
    """
    total = int(inside.sum())
    if total == 0:
        return None
    values = index_array[inside]
    values = values[np.isfinite(values)]
    valid = int(values.size)
    out = {
        "median": None, "mean": None, "p10": None, "p90": None,
        "valid_pixels": valid, "total_pixels": total,
        "valid_fraction": valid / total, "mask": mask_name,
    }
    if valid:
        out["median"] = float(np.median(values))
        out["mean"] = float(np.mean(values))
        out["p10"] = float(np.percentile(values, 10))
        out["p90"] = float(np.percentile(values, 90))
    return out


def cloud_fraction(scl: Optional[np.ndarray], inside_parcel: np.ndarray) -> Optional[float]:
    """Share of parcel pixels classified cloud or shadow in the SCL.

    ``scl`` must already be on the shape of ``inside_parcel``. None without SCL
    or without parcel pixels.
    """
    if scl is None:
        return None
    total = int(inside_parcel.sum())
    if total == 0:
        return None
    cloudy = np.isin(scl[inside_parcel].astype(int), list(CLOUD_SCL_CLASSES))
    return float(cloudy.sum()) / total
