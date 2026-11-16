"""Technician notes follow the place where they were written.

A note keeps the geometry of the zone it was written on. When a zoning is
published, each note goes to the zone that covers most of that geometry, if
that zone covers at least ``MIN_OVERLAP`` of it; when several notes land on
the same zone, the most recent one wins.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable

from pyproj import Transformer
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform
from shapely.validation import make_valid

MIN_OVERLAP = 0.30


def _utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _geometry(geojson: dict) -> BaseGeometry | None:
    try:
        geom = shape(geojson)
    except Exception:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    return None if geom.is_empty else geom


def _location(entity: dict) -> dict | None:
    loc = entity.get("location")
    if isinstance(loc, dict) and loc.get("type") == "GeoProperty":
        loc = loc.get("value")
    return loc if isinstance(loc, dict) else None


def assign_notes(notes: Iterable[Any], entities: list[dict]) -> dict[str, str]:
    """``zone id -> cause`` for the zones that receive a note (rules in the module docstring)."""
    zones = [(e["id"], _geometry(loc)) for e in entities if (loc := _location(e)) and e.get("id")]
    zones = [(zid, g) for zid, g in zones if g is not None]
    if not zones:
        return {}
    # Areas in metres: one UTM zone for the parcel (zones of one parcel are a few km at most).
    anchor = zones[0][1].representative_point()
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{_utm_epsg(anchor.x, anchor.y)}", always_xy=True).transform
    zones_m = [(zid, shp_transform(to_utm, g)) for zid, g in zones]

    assigned: dict[str, str] = {}
    for note in sorted(notes, key=lambda n: n.created_at):  # later notes overwrite earlier ones
        geom = _geometry(note.geometry or {})
        if geom is None:
            continue
        note_m = shp_transform(to_utm, geom)
        if note_m.area <= 0:
            continue
        best_id, best_share = None, 0.0
        for zid, zone_m in zones_m:
            share = note_m.intersection(zone_m).area / note_m.area
            if share > best_share:
                best_id, best_share = zid, share
        if best_id is not None and best_share >= MIN_OVERLAP:
            assigned[best_id] = note.cause
    return assigned


def with_causes(entities: list[dict], causes: dict[str, str]) -> list[dict]:
    """Copies of ``entities`` with ``technicianCause`` set on the zones that have one."""
    out = []
    for entity in entities:
        e = copy.deepcopy(entity)
        if e.get("id") in causes:
            e["technicianCause"] = {"type": "Property", "value": causes[e["id"]]}
        out.append(e)
    return out
