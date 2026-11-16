"""AgriManagementZone entities of a multi-date proposal (contract agreed with FaST/gis-routing).

One entity per level; ``zoneLevel`` is a code (high/medium/low/critical) that
each interface translates: no human-language text goes into the broker.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform

from app.jobs.zoning_multidate.polygons import ZoneSummary

logger = logging.getLogger(__name__)

METHOD = "vegetationIndexMultiDate"
ZONE_TYPE = "AgriManagementZone"
MAX_ENTITY_BYTES = 900 * 1024  # Orion rejects bodies near 1 MB
_NKZ = "https://nkz-os.org/ns/"
_PAGE = 100


def _prop(value: Any) -> dict:
    return {"type": "Property", "value": value}


def _round_coords(obj: Any) -> Any:
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), 7) for v in obj]
        return [_round_coords(o) for o in obj]
    return obj


def build_entities(parcel_urn: str, run_id: str, index: str, dates: list[date],
                   zones: list[ZoneSummary], crs: str, now: datetime) -> list[dict]:
    short = parcel_urn.rsplit(":", 1)[-1]
    to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    entities = []
    for z in zones:
        geom = mapping(shp_transform(to_wgs84.transform, z.geometry_utm))
        geom = {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"])}
        entity = {
            "id": f"urn:ngsi-ld:{ZONE_TYPE}:{short}:{run_id[:8]}:L{z.rank}",
            "type": ZONE_TYPE,
            "hasAgriParcel": {"type": "Relationship", "object": parcel_urn},
            "location": {"type": "GeoProperty", "value": geom},
            "zoneId": _prop(z.rank),
            "zoneRank": _prop(z.rank),
            "zoneName": _prop(z.level),
            "zoneLevel": _prop(z.level),
            "relativeVigorPct": _prop(z.relative_pct),
            "confirmingDates": _prop(z.confirming),
            "totalDates": _prop(z.total),
            "sourceDates": _prop([d.isoformat() for d in dates]),
            "meanValue": _prop(round(z.mean_value, 4)),
            "areaHa": _prop(round(z.area_ha, 2)),
            "variableAttribute": _prop(index),
            "zoningRunId": _prop(run_id),
            "zoningMethod": _prop(METHOD),
            "dateZoned": _prop({"@type": "DateTime", "@value": stamp}),
        }
        if len(json.dumps(entity).encode()) > MAX_ENTITY_BYTES:
            raise ValueError(f"zone {entity['id']} exceeds the broker entity size limit")
        entities.append(entity)
    return entities


AUTOMATIC_METHODS = frozenset({"vegetationIndex", METHOD})


def publish(fiware, entities: list[dict]) -> None:
    """Create every zone, or none: a failure deletes the zones already created and raises."""
    created: list[str] = []
    for entity in entities:
        if not fiware.create_entity(entity):
            for eid in created:
                try:
                    fiware.delete_entity(eid)
                except Exception:
                    pass  # best effort; the run id differs, so it never mixes with a later one
            raise RuntimeError(f"broker refused zone {entity['id']}")
        created.append(entity["id"])


def _value(row: dict, name: str) -> Any:
    for key in (name, _NKZ + name, "nkz:" + name):
        if key in row:
            v = row[key]
            return v.get("value") if isinstance(v, dict) else v
    return None


def _list_parcel_zones(fiware, parcel_urn: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        response = fiware.session.get(
            f"{fiware.context_broker_url}/ngsi-ld/v1/entities",
            params={"type": ZONE_TYPE, "q": f'hasAgriParcel=="{parcel_urn}"', "options": "keyValues",
                    "attrs": "zoningMethod,zoningRunId,hasAgriParcel", "limit": _PAGE, "offset": offset},
            headers={"Accept": "application/json", "Content-Type": None},
            timeout=10,
        )
        response.raise_for_status()
        page = response.json()
        rows.extend(page)
        if len(page) < _PAGE:
            return rows
        offset += _PAGE


def _parcel_of(row: dict) -> Any:
    for key in ("hasAgriParcel", _NKZ + "hasAgriParcel", "refAgriParcel"):
        v = row.get(key)
        if isinstance(v, dict):
            v = v.get("object") or v.get("value")
        if v:
            return v
    return None


def retire_quietly(fiware, parcel_urn: str, keep_run_id: str | None) -> int:
    """``retire_automatic_zonings`` that logs a broker failure instead of raising.

    Used once the outcome is decided (a run published, or the zoning withdrawn): the tables
    record that outcome, and every later action on the parcel sweeps the leftovers again
    with the run in force kept, so a failure here is repaired by the next one.
    """
    try:
        return retire_automatic_zonings(fiware, parcel_urn, keep_run_id=keep_run_id)
    except Exception:
        logger.exception("zoning %s: older zones not fully retired (kept run %s)", parcel_urn, keep_run_id)
        return 0


def retire_automatic_zonings(fiware, parcel_urn: str, keep_run_id: str | None) -> int:
    """Delete this parcel's vegetation zonings, except ``keep_run_id``.

    Only known automatic methods go (never manual, never a zone whose method
    cannot be read); each row is re-checked against the parcel in case the
    broker's q filter misbehaves; the new run is protected by id and run id.
    """
    keep_prefix = None
    if keep_run_id:
        keep_prefix = f"urn:ngsi-ld:{ZONE_TYPE}:{parcel_urn.rsplit(':', 1)[-1]}:{keep_run_id[:8]}:"
    doomed = [
        row["id"] for row in _list_parcel_zones(fiware, parcel_urn)
        if row.get("id")
        and _parcel_of(row) == parcel_urn
        and _value(row, "zoningMethod") in AUTOMATIC_METHODS
        and not (keep_prefix and row["id"].startswith(keep_prefix))
        and (keep_run_id is None or _value(row, "zoningRunId") != keep_run_id)
    ]
    return sum(1 for eid in doomed if fiware.delete_entity(eid))
