"""Optical (Sentinel-2) ``EOSatelliteImagery`` written next to the legacy ``EOProduct``.

Gated per tenant by ``fiware_integration.satimg_enabled``; the legacy write never
depends on anything here. One entity is one take over one target and day, and
every write carries the full ``analytics`` list (the upsert replaces it), so each
call reads the stored entity through the SDK reader and decides:

- ``merge``: same take (same ``productID``, both without a scene, or the same
  composite window): values merged by name, the new ones win.
- ``replace``: another take that wins the day (better interior fraction, a scene
  over the Statistical API, another composite window): the entity is deleted and
  written again, so nothing of the losing take survives the attribute-merging upsert.
- ``skip``: another take that loses (worse interior fraction, the Statistical API
  against a scene).

Never raises: a failure is logged with tenant, target, family and day.
Read-modify-write is not atomic: correct while one worker writes a target at a time.
"""
from __future__ import annotations

import json
import logging
import math
import numbers
import re
from dataclasses import dataclass, replace as dc_replace
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

from nkz_platform_sdk import parse_eo_entities

from app.services import fiware_integration as fi
from app.services.eo_satellite_imagery import (
    INDEX_NAMES, ZONE_RELATION, Family, RasterMeta, SceneMeta, Sensing, Target, build_entity, entity_id,
)

logger = logging.getLogger(__name__)

CREATE, MERGE, REPLACE, SKIP = "create", "merge", "replace", "skip"
WRITTEN = frozenset({CREATE, MERGE, REPLACE})

S2_COLLECTION = "sentinel-2-l2a"
_S2_ID = re.compile(r"^(S2[A-D])_MSIL2A_\d{8}T\d{6}_N\d{4}_R(\d{3})_")
_NO_GEOMETRY = " The processed geometry was not available."
_DESCRIPTION_KEYS = ("resultDescription", "https://smartdatamodels.org/dataModel.SatelliteImagery/resultDescription",
                     "https://uri.etsi.org/ngsi-ld/default-context/resultDescription")
_EXPLANATION = {
    "ndvi": "NDVI, Normalized Difference Vegetation Index, -1 to 1",
    "evi": "EVI, Enhanced Vegetation Index, -1 to 1",
    "savi": "SAVI, Soil Adjusted Vegetation Index, -1 to 1",
    "osavi": "OSAVI, Optimized Soil Adjusted Vegetation Index, -1 to 1",
    "gndvi": "GNDVI, Green Normalized Difference Vegetation Index, -1 to 1",
    "ndre": "NDRE, Normalized Difference Red Edge index, -1 to 1",
    "ndmi": "NDMI, Normalized Difference Moisture Index, -1 to 1",
}


@dataclass(frozen=True)
class OpticalTake:
    """What a write is about: family, day (and time), scene, description and raster folder."""

    family: Family
    sensing: Sensing
    scene: Optional[SceneMeta]
    description: str
    raster_folder: Optional[str] = None


@dataclass(frozen=True)
class ZoneWrite:
    """The parcel's take and verdict, handed to the zone products of the same calculation."""

    take: OpticalTake
    parcel_verdict: str


# ── takes ───────────────────────────────────────────────────────────────────


def _geojson(footprint: Any) -> Optional[dict]:
    """GeoJSON of a scene footprint given as a dict, a JSON string or a PostGIS element."""
    if footprint is None:
        return None
    if isinstance(footprint, Mapping):
        return dict(footprint)
    try:
        if isinstance(footprint, str):
            return json.loads(footprint)
        from geoalchemy2.shape import to_shape
        from shapely.geometry import mapping
        return json.loads(json.dumps(mapping(to_shape(footprint))))
    except Exception as exc:
        logger.warning("eo_satimg: scene footprint unreadable, omitted: %s", exc)
        return None


def _sensing(day: date, moment: Any) -> Sensing:
    """The day, with the real acquisition time only when it is UTC-aware and on that day."""
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if isinstance(moment, datetime) and moment.tzinfo is not None:
        if moment.astimezone(timezone.utc).date() == day:
            return Sensing(day, moment)
    return Sensing(day, None)


def _scene_meta(product_id: Optional[str], footprint: Any) -> Optional[SceneMeta]:
    if not product_id:
        return None
    from app.services.copernicus_client import CopernicusDataSpaceClient

    match = _S2_ID.match(product_id)
    return SceneMeta(
        product_id=product_id,
        product_url=f"{CopernicusDataSpaceClient.CATALOG_URL}/collections/{S2_COLLECTION}/items/{product_id}",
        product_format="SAFE",
        product_type="S2MSI2A",
        platform_name=match.group(1) if match else "",
        orbit_number=int(match.group(2)) if match else None,
        operational_mode="MSI",
        footprint=_geojson(footprint),
    )


def scene_take(scene: Any, raster_folder: Optional[str] = None) -> OpticalTake:
    """Single-scene take from a ``VegetationScene`` (or a row with the same fields)."""
    meta = _scene_meta(scene.scene_id, scene.footprint)
    return OpticalTake(
        family=Family.S2_INDICES,
        sensing=_sensing(scene.sensing_date, scene.acquisition_datetime),
        scene=meta,
        description="Zonal vegetation indices from one Sentinel-2 L2A scene.",
        raster_folder=raster_folder,
    )


def stac_take(item: Mapping[str, Any], day: date) -> OpticalTake:
    """Single-scene take from a STAC search result (no stored raster)."""
    return OpticalTake(
        family=Family.S2_INDICES,
        sensing=_sensing(day, item.get("datetime")),
        scene=_scene_meta(item.get("id"), item.get("geometry")),
        description="Zonal vegetation indices from one Sentinel-2 L2A scene.",
    )


def no_scene_take(day: date) -> OpticalTake:
    """Statistical API result: a day, no identified scene, no raster."""
    return OpticalTake(
        family=Family.S2_INDICES,
        sensing=Sensing(day, None),
        scene=None,
        description=("Zonal vegetation indices of the day from the Sentinel Hub Statistical API "
                     "over Sentinel-2 L2A; no single scene is identified."),
    )


def composite_take(start: date, end: date, raster_folder: Optional[str] = None) -> OpticalTake:
    """Temporal composite: dated by the LAST day of its window, never by a scene."""
    return OpticalTake(
        family=Family.S2_COMPOSITE,
        sensing=Sensing(end, None),
        scene=None,
        description=(f"Zonal vegetation indices of the median temporal composite of Sentinel-2 L2A "
                     f"scenes from {start.isoformat()} to {end.isoformat()}."),
        raster_folder=raster_folder,
    )


def raster_folder(bucket: str, key: Optional[str], index_type: str) -> Optional[str]:
    """``s3://bucket/folder`` when the raster follows ``{folder}/{name}.tif``, else None.

    Rasters stored before the rename (``NDVI.tif``) are not declared: the reader would
    point at ``ndvi.tif``, which does not exist (object keys are case-sensitive).
    """
    if not key or "/" not in key:
        return None
    folder, name = key.rsplit("/", 1)
    if name != f"{index_type.lower()}.tif" or index_type.lower() not in INDEX_NAMES:
        return None
    return f"s3://{bucket}/{folder}"


def parcel_target(parcel_urn: str, geometry: Optional[Mapping]) -> Target:
    return Target("parcel", parcel_urn, None, None, _polygon(geometry))


def zone_target(parcel_urn: str, zone_urn: str, geometry: Optional[Mapping]) -> Target:
    return Target("zone", parcel_urn, zone_urn, ZONE_RELATION, _polygon(geometry))


def _polygon(geometry: Optional[Mapping]) -> Optional[Mapping]:
    return geometry if isinstance(geometry, Mapping) and "coordinates" in geometry else None


# ── analytics ───────────────────────────────────────────────────────────────


def index_analytics(index_type: str, statistics: Mapping[str, Any], *,
                    interior: Optional[Mapping[str, Any]] = None) -> dict:
    """One index: mean as the value, its min/max/std and, if measured, its interior statistics."""
    idx = index_type.lower()
    out = {idx: statistics.get("mean")}
    for stat in ("min", "max", "std"):
        out[f"{idx}.{stat}"] = statistics.get(stat)
    if interior:
        out[f"{idx}.interiorValidFraction"] = interior.get("valid_fraction")
        out[f"{idx}.interiorPixelCount"] = interior.get("valid_pixels")
        out[f"{idx}.interiorMask"] = interior.get("mask")
        for name, key in (("interiorMean", "mean"), ("interiorMedian", "median"),
                          ("interiorP10", "p10"), ("interiorP90", "p90")):
            out[f"{idx}.{name}"] = interior.get(key)
    return out


def cloud_coverage(value: Any) -> Optional[float]:
    """Scene cloud cover in %, or None when it is not a number (e.g. ``"N/A"``)."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ── the write ───────────────────────────────────────────────────────────────


def _read(tenant: str, eid: str):
    raw = fi.get_eo_satellite_imagery(tenant, eid)
    if raw is None:
        return None, None
    found = parse_eo_entities([raw])
    return raw, (found[0] if found else None)


def _description(raw: Mapping[str, Any]) -> str:
    for key in _DESCRIPTION_KEYS:
        attr = raw.get(key)
        value = attr.get("value") if isinstance(attr, Mapping) else attr
        if isinstance(value, str):
            return value
    return ""


def _same_take(raw, obs, take: OpticalTake) -> bool:
    if take.family is Family.S2_COMPOSITE:
        return _description(raw).startswith(take.description)
    return obs.product_id == (take.scene.product_id if take.scene else None)


def _stored_fraction(values: Mapping[str, Any], idx: str) -> Optional[float]:
    own = values.get(f"{idx}.interiorValidFraction")
    if isinstance(own, (int, float)):
        return float(own)
    others = [v for n, v in values.items() if n.endswith(".interiorValidFraction") and isinstance(v, (int, float))]
    return float(max(others)) if others else None


def _verdict(raw, obs, take: OpticalTake, idx: str, fraction: Optional[float], follow: Optional[str]) -> str:
    if raw is None:
        return CREATE
    if obs is None:
        return REPLACE  # unreadable: our own id, rewritten whole
    if _same_take(raw, obs, take):
        return MERGE
    if follow is not None:
        return REPLACE  # a zone follows the verdict of its parcel's take
    if take.family is Family.S2_COMPOSITE:
        # ASSUMPTION: two windows ending the same day: the latest computed wins.
        return REPLACE
    if take.scene is None:
        return SKIP  # the Statistical API never displaces a scene
    if obs.product_id is None:
        return REPLACE  # a scene over the Statistical API
    stored = _stored_fraction(obs.values, idx)
    if stored is None:
        return REPLACE  # nothing to defend (same rule as is_better_observation)
    if fraction is None:
        return SKIP
    return REPLACE if fraction > stored else SKIP


def _present(value: Any) -> bool:
    """None and non-finite numbers are "no value": they never overwrite a stored one."""
    if value is None:
        return False
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return math.isfinite(float(value))
    return True


def write_optical(tenant: str, target: Target, take: OpticalTake, analytics: Mapping[str, Any], *,
                  index_type: str, interior_fraction: Optional[float] = None,
                  follow: Optional[str] = None, merge_only: bool = False) -> Optional[str]:
    """Write one optical value set into the take's entity.

    Returns the verdict (create/merge/replace/skip), or None when the tenant is
    switched off or the write failed (logged). ``follow`` is the parcel's verdict
    for a zone write; ``merge_only`` allows nothing but completing the same take.
    """
    if not fi.satimg_enabled(tenant):
        return None
    fresh = {k: v for k, v in analytics.items() if _present(v)}
    target_urn = target.zone_urn if target.kind == "zone" else target.parcel_urn
    context = (f"tenant={tenant} target={target_urn} family={take.family.value} "
               f"day={take.sensing.day.isoformat()}")
    try:
        if not fi.has_valid_pixels({"pixel_count": fresh.get("pixelCount")}):
            logger.info("eo_satimg: %s %s not written: no valid pixels", context, index_type.lower())
            return SKIP
        eid = entity_id(tenant, target.kind, target_urn, take.family, take.sensing.day)
        raw, obs = _read(tenant, eid)
        idx = index_type.lower()
        verdict = _verdict(raw, obs, take, idx, interior_fraction, follow)
        if verdict == SKIP or (merge_only and verdict != MERGE):
            logger.info("eo_satimg: %s %s not written, another take wins the day", context, idx)
            return SKIP
        merging = verdict == MERGE
        values = dict(obs.values) if merging else {}
        values.update(fresh)
        sensing = take.sensing
        if merging and sensing.time_utc is None and obs.sensing_time is not None:
            sensing = Sensing(sensing.day, obs.sensing_time)
        folder = take.raster_folder or (obs.raster_path if merging else None)
        raster = None
        if folder:
            raster = RasterMeta(folder, [(n, _EXPLANATION[n]) for n in INDEX_NAMES if n in values])
        entity = build_entity(
            tenant=tenant, target=target, family=take.family, sensing=sensing, analytics=values,
            scene=take.scene, raster=raster,
            result_description=take.description + ("" if target.geometry else _NO_GEOMETRY),
        )
        if entity is None:
            return SKIP  # the builder logged why
        if raw is not None and verdict == REPLACE and not fi.delete_eo_satellite_imagery(tenant, eid):
            logger.error("eo_satimg: %s not replaced, delete of the stored take failed", context)
            return None
        if fi.upsert_eo_satellite_imagery(tenant, entity) is None:
            logger.error("eo_satimg: %s write failed", context)
            return None
        return verdict
    except Exception as exc:
        logger.error("eo_satimg: %s write failed: %s", context, exc, exc_info=True)
        return None


# ── deletes (never gated by the switch) ─────────────────────────────────────


def job_family_day(result: Mapping[str, Any]) -> Optional[tuple]:
    """(family, day) of a calculation's take: a composite is dated by its window end."""
    try:
        if result.get("is_composite"):
            end = (result.get("date_range") or {}).get("end")
            return (Family.S2_COMPOSITE, date.fromisoformat(str(end)[:10])) if end else None
        sensing = result.get("sensing_date")
        return (Family.S2_INDICES, date.fromisoformat(str(sensing)[:10])) if sensing else None
    except ValueError:
        return None


def _is_job_take(raw, obs, result: Mapping[str, Any]) -> bool:
    """Whether the stored entity holds the take of this calculation.

    A deleted job of a losing scene, or of the Statistical API, must not erase the winner's values.
    """
    if obs.family == Family.S2_COMPOSITE.value:
        window = result.get("date_range") or {}
        take = composite_take(date.fromisoformat(str(window["start"])[:10]), obs.sensing_day)
        return _same_take(raw, obs, take)
    scene_uuid = result.get("scene_id")
    if not scene_uuid:
        return obs.product_id is None
    if obs.target == "zone":  # zones keep no raster: only a job that lost its day is told apart
        return result.get("skip_reason") != "better_scene_same_date"
    stored = re.search(r"/scenes/([^/]+)$", (obs.raster_path or "").rstrip("/"))
    return stored is None or stored.group(1) == str(scene_uuid)


def remove_index(tenant: str, target_kind: str, target_urn: str, family: Family, day: date,
                 index_type: str, job_result: Optional[Mapping[str, Any]] = None) -> bool:
    """Drop one index and its statistics from the entity; delete the entity once no index is left.

    Same rule as the legacy ``delete_eo_index``: one index job removes its own values,
    never the other indices of the take. With ``job_result`` the entity is left alone when it
    holds another take. Missing entity = done. Never raises.
    """
    idx = index_type.lower()
    context = f"tenant={tenant} target={target_urn} family={Family(family).value} day={day.isoformat()}"
    try:
        eid = entity_id(tenant, target_kind, target_urn, family, day)
        raw, obs = _read(tenant, eid)
        if raw is None:
            return True
        if obs is None:
            logger.warning("eo_satimg: %s unreadable, %s not removed", context, idx)
            return False
        if job_result is not None and not _is_job_take(raw, obs, job_result):
            logger.info("eo_satimg: %s %s kept, the entity holds another take", context, idx)
            return True
        values = {n: v for n, v in obs.values.items() if n.split(".", 1)[0] != idx}
        if len(values) == len(obs.values):
            return True
        if not any(n in values for n in INDEX_NAMES):
            return fi.delete_eo_satellite_imagery(tenant, eid)
        zone = obs.target == "zone"
        target = Target(obs.target, obs.parcel_id, obs.zone_id if zone else None,
                        ZONE_RELATION if zone else None, None)
        layers = [(n, _EXPLANATION[n]) for n in INDEX_NAMES if n in values]
        entity = build_entity(
            tenant=tenant, target=target, family=Family(obs.family),
            sensing=Sensing(obs.sensing_day, obs.sensing_time), analytics=values, scene=None,
            raster=RasterMeta(obs.raster_path, layers) if obs.raster_path else None,
            result_description=_description(raw),
        )
        if entity is None:
            return fi.delete_eo_satellite_imagery(tenant, eid)
        # Attributes not sent stay as stored (product, geometry); only the lists change.
        entity.pop("productType", None)
        if fi.upsert_eo_satellite_imagery(tenant, entity) is None:
            return False
        stored_layers = [k for k in raw if k.rsplit("/", 1)[-1] == "contentInformation"]
        if stored_layers and "contentInformation" not in entity:
            fi.delete_eo_attribute(tenant, eid, stored_layers[0])  # fewer than two layers left
        return True
    except Exception as exc:
        logger.error("eo_satimg: %s removing %s failed: %s", context, idx, exc, exc_info=True)
        return False


def remove_job_index(tenant: str, parcel_urn: str, result: Mapping[str, Any], index_type: str) -> bool:
    """Remove a deleted calculation's index from its parcel entity (the zones go with delete_zone_products)."""
    family_day = job_family_day(result or {})
    if family_day is None or not parcel_urn:
        return True
    return remove_index(tenant, "parcel", parcel_urn, family_day[0], family_day[1], index_type, result)


def zone_take(take: OpticalTake) -> OpticalTake:
    """Zone entities keep no raster."""
    return dc_replace(take, raster_folder=None)
