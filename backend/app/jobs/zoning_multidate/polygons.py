"""Level polygons clipped exactly to the parcel, and a summary per level.

shapely 2.0 has no coverage simplification, so each level is simplified on its
own and the coverage is rebuilt without overlaps: levels claim area in rank
order and the largest level takes whatever is left of the parcel. The areas
therefore always add up to the parcel.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from rasterio.features import shapes
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.jobs.zoning_multidate import config as cfg
from app.jobs.zoning_multidate.grid import Grid

LEVEL_NAMES = {1: "high", 2: "medium", 3: "low", 4: "critical"}


def _polygonal(geom: BaseGeometry, min_area: float) -> BaseGeometry | None:
    if geom.is_empty:
        return None
    parts = getattr(geom, "geoms", [geom])
    polys = []
    for g in parts:
        if g.geom_type == "Polygon" and g.area >= min_area:
            polys.append(g)
        elif g.geom_type == "MultiPolygon":
            polys.extend(p for p in g.geoms if p.area >= min_area)
    if not polys:
        return None
    return unary_union(polys)


def level_polygons(levels: np.ndarray, grid: Grid) -> dict[int, BaseGeometry]:
    res = abs(grid.transform.a)
    pixel = res * res
    simplified: dict[int, BaseGeometry] = {}
    for rank in sorted(int(r) for r in np.unique(levels) if r > 0):
        mask = levels == rank
        polys = [shape(g) for g, v in shapes(mask.astype("uint8"), mask=mask, transform=grid.transform)
                 if v == 1]
        # One-pixel tolerance turns the 10 m staircase into diagonals (owner, 2026-11-16).
        merged = unary_union(polys).simplify(res, preserve_topology=True)
        simplified[rank] = merged.intersection(grid.parcel_utm)
    if not simplified:
        return {}
    largest = max(simplified, key=lambda r: (simplified[r].area, -r))
    out: dict[int, BaseGeometry] = {}
    claimed = None
    # Critical claims first: a small critical patch must not be eaten by its neighbour.
    for rank in sorted((r for r in simplified if r != largest), key=lambda r: (r != 4, r)):
        geom = simplified[rank] if claimed is None else simplified[rank].difference(claimed)
        geom = _polygonal(geom, pixel)
        if geom is None:
            continue
        out[rank] = geom
        claimed = geom if claimed is None else unary_union([claimed, geom])
    rest = grid.parcel_utm if claimed is None else grid.parcel_utm.difference(claimed)
    rest = _polygonal(rest, 0.0)
    if rest is not None:
        keep, slivers = [], []
        for part in getattr(rest, "geoms", [rest]):
            (keep if part.area >= pixel else slivers).append(part)
        # Gaps left between simplified borders go to the neighbour they touch most.
        for part in slivers:
            touching = [(out[r].intersection(part.buffer(0.01)).area, -r, r) for r in out]
            best = max(touching, default=None)
            if best and best[0] > 0:
                out[best[2]] = unary_union([out[best[2]], part])
            else:
                keep.append(part)
        if keep:
            out[largest] = unary_union(keep)
    return dict(sorted(out.items()))


@dataclass
class ZoneSummary:
    rank: int
    level: str
    geometry_utm: BaseGeometry
    area_ha: float
    mean_value: float
    relative_pct: int
    confirming: int
    total: int


def _confirms(rank: int, mean_rel: float) -> bool:
    if rank == 1:
        return mean_rel > 1.0
    if rank in (3, 4):
        return mean_rel < 1.0
    return cfg.LOW_MAX <= mean_rel < cfg.HIGH_MIN


def summarize(levels: np.ndarray, stack: np.ndarray, rel: np.ndarray,
              polygons: dict[int, BaseGeometry], core: np.ndarray | None = None) -> list[ZoneSummary]:
    """Statistics per level, measured on ``core`` pixels (the mixed border ring is skipped)."""
    zones: list[ZoneSummary] = []
    for rank, geom in sorted(polygons.items()):
        mask = levels == rank
        if core is not None and (mask & core).any():
            mask = mask & core
        if not mask.any():
            continue
        per_date = [float(np.nanmean(day[mask])) for day in rel if np.isfinite(day[mask]).any()]
        mean_rel = float(np.mean(per_date)) if per_date else 1.0
        zones.append(ZoneSummary(
            rank=rank,
            level=LEVEL_NAMES[rank],
            geometry_utm=geom,
            area_ha=geom.area / 10_000.0,
            mean_value=float(np.nanmean(stack[:, mask])),
            relative_pct=int(round((mean_rel - 1.0) * 100.0)),
            confirming=sum(1 for m in per_date if _confirms(rank, m)),
            total=len(per_date),
        ))
    return zones
