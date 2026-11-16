"""Merge two levels of a zoning into one, chosen by the technician.

The merged zone takes the level the technician picked (one of the two), the
union of both geometries, its area recomputed from that geometry, mean value
and relative vigour weighted by area, and the smaller of the two date counts.
Every zone of the result belongs to a new run derived from the old one and is
born accepted.
"""
from __future__ import annotations

import copy
from datetime import datetime

from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.jobs.zoning_multidate.notes import _utm_epsg
from app.jobs.zoning_multidate.publish import ZONE_TYPE, _round_coords


def _p(value):
    return {"type": "Property", "value": value}


def _v(entity: dict, name: str):
    attr = entity.get(name)
    return attr.get("value") if isinstance(attr, dict) else attr


def _polygonal(geom):
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    return unary_union(parts)


def merge_levels(entities: list[dict], ranks: tuple[int, int], target_rank: int, new_run_id: str,
                 parent_run_id: str, now: datetime) -> list[dict]:
    a, b = ranks
    if a == b or target_rank not in (a, b):
        raise ValueError("merge two different levels into one of them")
    by_rank = {_v(e, "zoneRank"): e for e in entities}
    if a not in by_rank or b not in by_rank:
        raise ValueError("both levels must exist in the zoning")
    dropped_rank = b if target_rank == a else a
    target, other = by_rank[target_rank], by_rank[dropped_rank]

    geom = _polygonal(make_valid(unary_union([shape(target["location"]["value"]),
                                              shape(other["location"]["value"])])))
    anchor = geom.representative_point()
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{_utm_epsg(anchor.x, anchor.y)}", always_xy=True)
    area_ha = shp_transform(to_utm.transform, geom).area / 10_000.0
    wt, wo = float(_v(target, "areaHa") or 0), float(_v(other, "areaHa") or 0)
    total = (wt + wo) or 1.0

    def weighted(name: str) -> float:
        return (float(_v(target, name)) * wt + float(_v(other, name)) * wo) / total

    merged = copy.deepcopy(target)
    gj = mapping(geom)
    merged["location"] = {"type": "GeoProperty",
                          "value": {"type": gj["type"], "coordinates": _round_coords(gj["coordinates"])}}
    merged["areaHa"] = _p(round(area_ha, 2))
    merged["meanValue"] = _p(round(weighted("meanValue"), 4))
    merged["relativeVigorPct"] = _p(round(weighted("relativeVigorPct")))
    merged["confirmingDates"] = _p(min(_v(target, "confirmingDates"), _v(other, "confirmingDates")))
    merged["totalDates"] = _p(min(_v(target, "totalDates"), _v(other, "totalDates")))
    if "technicianCause" not in merged and "technicianCause" in other:
        merged["technicianCause"] = copy.deepcopy(other["technicianCause"])

    short = merged["hasAgriParcel"]["object"].rsplit(":", 1)[-1]
    stamp = _p({"@type": "DateTime", "@value": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    out = []
    for rank in sorted(by_rank):
        if rank == dropped_rank:
            continue
        zone = merged if rank == target_rank else copy.deepcopy(by_rank[rank])
        zone["id"] = f"urn:ngsi-ld:{ZONE_TYPE}:{short}:{new_run_id[:8]}:L{rank}"
        zone["zoningRunId"] = _p(new_run_id)
        zone["derivedFromRunId"] = _p(parent_run_id)
        zone["acceptedAt"] = copy.deepcopy(stamp)
        zone["dateZoned"] = copy.deepcopy(stamp)
        out.append(zone)
    return out
