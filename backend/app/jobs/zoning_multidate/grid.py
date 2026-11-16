"""A common UTM grid per parcel: every index date is resampled onto it.

Index rasters come from two engines with different grids (local: Sentinel-2
UTM window with a buffer; Copernicus: EPSG:4326, ~0.0001 deg), so dates are
never stacked as they come.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform, unary_union
from shapely.validation import make_valid

LEGACY_NODATA = -9999.0


@dataclass
class Grid:
    crs: str
    transform: Affine
    shape: tuple[int, int]
    parcel_utm: BaseGeometry
    inside: np.ndarray  # all_touched mask of the parcel
    # Pixels whose centre is at least one pixel inside the boundary. The ring
    # between ``inside`` and ``core`` mixes crop with tracks or the next field,
    # so vigour is measured on ``core`` and the ring takes its neighbour's level.
    core: np.ndarray


def utm_crs(lon: float, lat: float) -> str:
    zone = int(math.floor((lon + 180.0) / 6.0) + 1)
    return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"


def _polygonal(geom: BaseGeometry) -> BaseGeometry:
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        geom = unary_union([g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("parcel geometry has no polygonal area")
    return geom


def parcel_grid(parcel_wgs84: dict[str, Any], res_m: float) -> Grid:
    """UTM grid aligned to ``res_m`` covering the parcel (plus one cell)."""
    geom = _polygonal(shape(parcel_wgs84))
    centroid = geom.centroid
    crs = utm_crs(centroid.x, centroid.y)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    parcel_utm = _polygonal(shp_transform(to_utm.transform, geom))
    minx, miny, maxx, maxy = parcel_utm.bounds
    x0 = math.floor(minx / res_m) * res_m - res_m
    y0 = math.ceil(maxy / res_m) * res_m + res_m
    width = int(math.ceil((maxx + res_m - x0) / res_m))
    height = int(math.ceil((y0 - (miny - res_m)) / res_m))
    transform = Affine(res_m, 0.0, x0, 0.0, -res_m, y0)
    inside = geometry_mask([parcel_utm], out_shape=(height, width), transform=transform,
                           invert=True, all_touched=True)
    shrunk = parcel_utm.buffer(-res_m)
    core = np.zeros_like(inside)
    if not shrunk.is_empty:
        core = geometry_mask([shrunk], out_shape=(height, width), transform=transform,
                             invert=True, all_touched=False)
    if not core.any():  # parcel too narrow to have a core: use all of it
        core = inside.copy()
    return Grid(crs=crs, transform=transform, shape=(height, width), parcel_utm=parcel_utm,
                inside=inside, core=core & inside)


def _read(ds) -> np.ndarray:
    band = ds.read(1).astype("float32")
    bad = ~np.isfinite(band) | (band == LEGACY_NODATA)
    if ds.nodata is not None and not (isinstance(ds.nodata, float) and math.isnan(ds.nodata)):
        bad |= band == ds.nodata
    band[bad] = np.nan
    return band


def read_onto_grid(src: str | bytes, grid: Grid) -> np.ndarray:
    """The raster resampled (bilinear) onto ``grid``; NaN where there is no data."""
    dst = np.full(grid.shape, np.nan, dtype="float32")

    def _warp(ds) -> None:
        reproject(
            source=_read(ds), destination=dst,
            src_transform=ds.transform, src_crs=ds.crs, src_nodata=np.nan,
            dst_transform=grid.transform, dst_crs=grid.crs, dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

    if isinstance(src, (bytes, bytearray)):
        with MemoryFile(bytes(src)) as mem, mem.open() as ds:
            _warp(ds)
    else:
        with rasterio.open(src) as ds:
            _warp(ds)
    return dst
