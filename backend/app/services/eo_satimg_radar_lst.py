"""Radar (Sentinel-1) and surface temperature ``EOSatelliteImagery`` written next to the legacy ``EOProduct``.

Gated per tenant by ``fiware_integration.satimg_enabled``; the legacy write never depends on anything
here and nothing here raises: a failure is logged with tenant, target, family and day.

One entity is one take over one target and day (contract section 1). Radar has three families written
from one S1 scene (``s1-backscatter``, ``s1-soil-moisture``, ``s1-change``); surface temperature has one
per source (``landsat-lst``, ``clms-lst``). Every write carries the full ``analytics`` list (the upsert
replaces it), so each call reads the stored entity through the SDK reader and decides:

- ``merge``: same take (same ``productID``): stored values merged by name, the new ones win.
- ``replace``: another take that wins the day (larger ``pixelCount``; for ``s1-change``, the take whose
  backscatter won): the whole list and the product attributes are rewritten, then the optional
  attributes only the losing take had are pruned (upsert first, prune afterwards).
- ``skip``: another take that loses (smaller or equal ``pixelCount``: a tie keeps the stored one).

No raster is declared (``localServerPath`` / ``contentInformation``): the stored SAR COG names do not
follow the contract's ``{name}.tif`` rule. Read-modify-write is not atomic: correct while one worker
writes a target at a time.
"""
from __future__ import annotations

import logging
import numbers
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Optional

from app.services import fiware_integration as fi
from app.services.eo_satellite_imagery import Family, SceneMeta, Sensing, Target, build_entity, entity_id
from app.services.eo_satimg_optical import (
    CREATE, MERGE, NO_GEOMETRY, REPLACE, SKIP, WRITTEN, footprint_geojson, make_sensing, parcel_target,
    present, prune_leftovers, read_entity, zone_target,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CREATE", "MERGE", "REPLACE", "SKIP", "WRITTEN", "parcel_target", "zone_target", "RadarTake", "LstTake",
    "scene_job_params", "complete_params", "radar_take", "landsat_take", "clms_take", "write_backscatter",
    "write_soil_moisture", "write_change", "write_lst",
]

S1_COLLECTION = "sentinel-1-grd"
_S1_ID = re.compile(r"^(S1[A-D])_([A-Z0-9]{2})_")
_S1_PLATFORM = re.compile(r"^sentinel-1([a-d])$")
_LANDSAT_ID = re.compile(r"^L[A-Z](\d{2})_(L2S[PR])_")

_DESC_BACKSCATTER = "Calibrated Sentinel-1 GRD backscatter (sigma nought, dB) over the target."
_DESC_SOIL_MOISTURE = "Soil moisture index (0 to 1) from Sentinel-1 GRD VV backscatter over the target."
_DESC_CHANGE = "Backscatter change of the parcel against the previous Sentinel-1 GRD take."
_DESC_LANDSAT = "Land surface temperature (degrees Celsius) from one Landsat Collection 2 Level-2 scene."
_DESC_CLMS = "Land surface temperature (degrees Celsius) from one CLMS hourly 3 km product."


# ── scene metadata, defined once ────────────────────────────────────────────


def scene_job_params(scene: Mapping[str, Any]) -> dict:
    """The job parameters that carry a Sentinel-1 scene's metadata (the one definition the dispatchers share).

    ``scene`` is a ``search_s1_scenes`` result. Only what the catalogue gave is returned, never a guess.
    ``orbit_direction`` is the key the task already read for the SMI.
    """
    state = scene.get("orbit_state")
    pairs = {
        "scene_datetime": scene.get("datetime"),
        "scene_platform": scene.get("platform"),
        "scene_relative_orbit": scene.get("relative_orbit"),
        "orbit_direction": state.upper() if isinstance(state, str) and state else None,
        "scene_footprint": scene.get("geometry"),
        "instrument_mode": scene.get("instrument_mode"),
    }
    return {k: v for k, v in pairs.items() if v not in (None, "")}


def complete_params(params: Mapping[str, Any], summary: Any) -> dict:
    """``params`` plus the scene keys it lacks, taken from a catalogue summary; what is there is kept."""
    out = dict(params)
    if isinstance(summary, Mapping):
        for key, value in scene_job_params(summary).items():
            out.setdefault(key, value)
    return out


def _day(value: Any) -> Optional[date]:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _whole(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


@dataclass(frozen=True)
class RadarTake:
    """One Sentinel-1 scene: its sensing day (and real time) and its catalogue metadata."""

    sensing: Sensing
    scene: SceneMeta


def radar_take(scene_id: Optional[str], params: Mapping[str, Any]) -> Optional[RadarTake]:
    """The take of a Sentinel-1 job, or None without a real sensing day (never a date of today)."""
    if not scene_id:
        return None
    day = _day(params.get("sensing_date")) or _day(params.get("scene_datetime"))
    if day is None:
        logger.warning("eo_satimg: no real sensing day for scene %s, new form not written", scene_id)
        return None
    from app.services.copernicus_client import CopernicusDataSpaceClient

    id_match = _S1_ID.match(scene_id)
    platform = id_match.group(1) if id_match else ""
    if not platform and isinstance(params.get("scene_platform"), str):
        named = _S1_PLATFORM.match(params["scene_platform"].lower())
        platform = f"S1{named.group(1).upper()}" if named else ""
    mode = params.get("instrument_mode")
    if not (isinstance(mode, str) and mode):
        mode = id_match.group(2) if id_match else None
    scene = SceneMeta(
        product_id=scene_id,
        product_url=f"{CopernicusDataSpaceClient.CATALOG_URL}/collections/{S1_COLLECTION}/items/{scene_id}",
        product_format="",  # the bands are downloaded as per-polarisation GeoTIFFs: not claimed to be SAFE
        product_type="GRD",
        platform_name=platform,
        orbit_number=_whole(params.get("scene_relative_orbit")),
        operational_mode=mode or None,
        footprint=footprint_geojson(params.get("scene_footprint")),
    )
    return RadarTake(make_sensing(day, params.get("scene_datetime")), scene)


@dataclass(frozen=True)
class LstTake:
    family: Family
    sensing: Sensing
    scene: SceneMeta
    description: str


def _lst_sensing(item: Mapping[str, Any]) -> Optional[Sensing]:
    when = (item.get("properties") or {}).get("datetime")
    day = _day(when)
    if day is None:
        logger.warning("eo_satimg: no real sensing day for scene %s, new form not written", item.get("id"))
        return None
    return make_sensing(day, when)


def _real_id(item: Any) -> Optional[str]:
    scene_id = item.get("id") if isinstance(item, Mapping) else None
    return scene_id if isinstance(scene_id, str) and scene_id and scene_id != "unknown" else None


def landsat_take(item: Any) -> Optional[LstTake]:
    """Take of a Landsat C2 L2 surface temperature STAC item (``id``, ``properties``, ``geometry``)."""
    scene_id = _real_id(item)
    sensing = _lst_sensing(item) if scene_id else None
    if sensing is None:
        return None
    from app.services.lst_processor import CDSE_STAC_URL, LST_COLLECTION

    props = item.get("properties") or {}
    id_match = _LANDSAT_ID.match(scene_id)
    product_type = props.get("product:type") or (id_match.group(2) if id_match else "")
    platform = props.get("platform")
    if isinstance(platform, str) and platform:
        platform = platform.upper().replace("-", "_")
    else:
        platform = f"LANDSAT_{int(id_match.group(1))}" if id_match else ""
    scene = SceneMeta(
        product_id=scene_id,
        product_url=f"{CDSE_STAC_URL}/collections/{LST_COLLECTION}/items/{scene_id}",
        product_format="",
        product_type=product_type if isinstance(product_type, str) else "",
        platform_name=platform,
        orbit_number=_whole(props.get("sat:relative_orbit")),  # omitted unless the catalogue gives an orbit
        operational_mode=None,
        footprint=footprint_geojson(item.get("geometry")),
    )
    return LstTake(Family.LANDSAT_LST, sensing, scene, _DESC_LANDSAT)


def clms_take(item: Any) -> Optional[LstTake]:
    """Take of a CLMS hourly LST STAC item; the product id is the raw STAC id."""
    scene_id = _real_id(item)
    sensing = _lst_sensing(item) if scene_id else None
    if sensing is None:
        return None
    from app.services.clms_lst_processor import CDSE_STAC_URL, CLMS_LST_COLLECTION

    product_type = (item.get("properties") or {}).get("product:type")
    scene = SceneMeta(
        product_id=scene_id,
        product_url=f"{CDSE_STAC_URL}/collections/{CLMS_LST_COLLECTION}/items/{scene_id}",
        product_format="",
        product_type=product_type if isinstance(product_type, str) else "",
        platform_name="",
        orbit_number=None,
        operational_mode=None,
        footprint=footprint_geojson(item.get("geometry")),
    )
    return LstTake(Family.CLMS_LST, sensing, scene, _DESC_CLMS)


# ── the write ───────────────────────────────────────────────────────────────

Decide = Callable[[Any, Any, str, Mapping[str, Any]], str]


def _by_coverage(raw, obs, product_id: str, fresh: Mapping[str, Any]) -> str:
    """Same take: merge. Another take: the larger ``pixelCount`` wins, a tie keeps the stored one."""
    if raw is None:
        return CREATE
    if obs is None:
        return REPLACE  # unreadable: our own id, rewritten whole
    if obs.product_id == product_id:
        return MERGE
    stored = obs.values.get("pixelCount")
    if isinstance(stored, bool) or not isinstance(stored, (int, float)):
        return REPLACE  # nothing to defend
    return REPLACE if fresh["pixelCount"] > stored else SKIP


def _follows_backscatter(raw, obs, product_id: str, fresh: Mapping[str, Any]) -> str:
    """The change entity of a take whose backscatter won the day: it is the day's state, so another take goes."""
    if raw is None:
        return CREATE
    if obs is None:
        return REPLACE
    return MERGE if obs.product_id == product_id else REPLACE


def _write(tenant: str, target: Target, family: Family, sensing: Sensing, scene: SceneMeta,
           analytics: Mapping[str, Any], description: str, decide: Decide, *,
           needs_count: bool = True) -> Optional[str]:
    """Read, decide, build and upsert one entity. Returns the verdict, None when off or failed (logged)."""
    if not fi.satimg_enabled(tenant):
        return None
    target_urn = target.zone_urn if target.kind == "zone" else target.parcel_urn
    context = (f"tenant={tenant} target={target_urn} family={family.value} day={sensing.day.isoformat()}")
    try:
        fresh = {k: v for k, v in analytics.items() if present(v)}
        if needs_count:
            count = fresh.get("pixelCount")
            if isinstance(count, bool) or not isinstance(count, numbers.Real) or count < 1:
                logger.warning("eo_satimg: %s not written: no valid pixel count", context)
                return SKIP
        eid = entity_id(tenant, target.kind, target_urn, family, sensing.day)
        raw, obs = read_entity(tenant, eid)
        verdict = decide(raw, obs, scene.product_id, fresh)
        if verdict == SKIP:
            logger.info("eo_satimg: %s not written, another take wins the day", context)
            return SKIP
        merging = verdict == MERGE
        values = dict(obs.values) if merging else {}
        values.update(fresh)
        moment = sensing
        if merging and sensing.time_utc is None and obs.sensing_time is not None:
            moment = Sensing(sensing.day, obs.sensing_time)
        entity = build_entity(
            tenant=tenant, target=target, family=family, sensing=moment, analytics=values, scene=scene,
            raster=None, result_description=description + ("" if target.geometry else NO_GEOMETRY),
        )
        if entity is None:
            return SKIP  # the builder logged why
        if fi.upsert_eo_satellite_imagery(tenant, entity) is None:
            logger.error("eo_satimg: %s write failed", context)
            return None  # nothing pruned: the stored take stays
        if raw is not None and verdict == REPLACE:
            prune_leftovers(tenant, eid, raw, entity, context)
        return verdict
    except Exception as exc:
        logger.error("eo_satimg: %s write failed: %s", context, exc, exc_info=True)
        return None


# ── writers ─────────────────────────────────────────────────────────────────


def _backscatter_analytics(stats_by_pol: Mapping[str, Mapping[str, Any]]) -> dict:
    """VV and VH (whichever exist), and the coverage of the polarisation that covers least."""
    out: dict[str, Any] = {}
    counts, fractions = [], []
    for pol in ("VV", "VH"):
        stats = stats_by_pol.get(pol)
        if not stats or not present(stats.get("mean")):
            continue
        out[f"backscatter{pol}"] = stats["mean"]
        counts.append(stats.get("pixel_count"))
        fractions.append(stats.get("valid_fraction"))
    if counts and all(present(c) for c in counts):
        out["pixelCount"] = min(counts)
    if fractions and all(present(f) for f in fractions):
        out["validPixelFraction"] = min(fractions)
    return out


def write_backscatter(tenant: str, target: Target, take: RadarTake,
                      stats_by_pol: Mapping[str, Mapping[str, Any]]) -> Optional[str]:
    """``s1-backscatter`` of a parcel or zone. ``stats_by_pol``: ``{"VV"|"VH": {mean, pixel_count, valid_fraction}}``."""
    return _write(tenant, target, Family.S1_BACKSCATTER, take.sensing, take.scene,
                  _backscatter_analytics(stats_by_pol), _DESC_BACKSCATTER, _by_coverage)


def write_soil_moisture(tenant: str, target: Target, take: RadarTake, smi: Mapping[str, Any]) -> Optional[str]:
    """``s1-soil-moisture``: the index, its min/max/std and its pixel count (``smi`` as ``compute_smi`` returns it)."""
    analytics = {
        "soilMoistureIndex": smi.get("mean"),
        "soilMoistureIndex.min": smi.get("min"),
        "soilMoistureIndex.max": smi.get("max"),
        "soilMoistureIndex.std": smi.get("std"),
        "pixelCount": smi.get("pixel_count"),
    }
    return _write(tenant, target, Family.S1_SOIL_MOISTURE, take.sensing, take.scene, analytics,
                  _DESC_SOIL_MOISTURE, _by_coverage)


def write_change(tenant: str, target: Target, take: RadarTake, change: Mapping[str, Any],
                 backscatter_verdict: Optional[str]) -> Optional[str]:
    """``s1-change``: only when the same take's parcel backscatter was written (it has no pixel count to compare)."""
    if not fi.satimg_enabled(tenant):
        return None
    if backscatter_verdict not in WRITTEN:
        logger.info("eo_satimg: tenant=%s target=%s family=%s day=%s not written, the backscatter of this take "
                    "was not", tenant, target.parcel_urn, Family.S1_CHANGE.value, take.sensing.day.isoformat())
        return SKIP
    analytics = {
        "changeFlag": change.get("change_flag"),
        "changeConfidence": change.get("confidence"),
        "deltaVV": change.get("delta_vv"),
    }
    return _write(tenant, target, Family.S1_CHANGE, take.sensing, take.scene, analytics, _DESC_CHANGE,
                  _follows_backscatter, needs_count=False)


def write_lst(tenant: str, target: Target, take: LstTake, stats: Mapping[str, Any]) -> Optional[str]:
    """``landsat-lst`` or ``clms-lst`` of a parcel (``stats``: mean, min, max, std, pixel_count in degrees Celsius)."""
    analytics = {
        "lst": stats.get("mean"),
        "lst.min": stats.get("min"),
        "lst.max": stats.get("max"),
        "lst.std": stats.get("std"),
        "pixelCount": stats.get("pixel_count"),
    }
    return _write(tenant, target, take.family, take.sensing, take.scene, analytics, take.description,
                  _by_coverage)
