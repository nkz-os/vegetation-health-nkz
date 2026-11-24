"""Convert the stored parcel ``EOProduct`` entities into ``EOSatelliteImagery``.

    python -m app.jobs.eo_satimg_backfill --tenant T [--tenant T ...] [--apply] [--verify] [--json]

Dry run by default: it reads, plans and counts, and writes nothing. ``--apply`` writes. ``--verify`` compares,
per tenant and family, the old entities with the new ones (the set of index names and their values at the
contract's decimals, per parcel and day) and exits non-zero on any difference no counted reason explains.

Scope: PARCEL products only. Zone ``EOProduct`` of any kind are skipped and counted (``zones_skipped``).
Tenants are always explicit; there is no discovery. A tenant listed in ``EO_SATIMG_REVIEW_TENANTS``
(comma separated) is refused unless ``--allow-review`` is given, and ``--apply`` refuses to run when that
variable is not set at all (it may be set empty). ``--apply`` also refuses to run inside the nightly window
(01:15 to 03:00 UTC, the vegetation passes) and stops a tenant that is still running when the window opens.

How a legacy entity is read: the broker is asked for the full type IRI, without a Link header, with
``count=true``; the pages are checked against ``NGSILD-Results-Count`` and a mismatch aborts that tenant.
Values come from ``nkz_platform_sdk.parse_eo_entities`` applied to each legacy entity alone: nothing is
re-interpreted here. An entity the reader returns nothing for (a shell) is counted and left alone.

Scene metadata is resolved PER INDEX, because one legacy entity may mix indices of different scenes and of a
temporal composite: from the scene folder of the index's raster URL, else from the completed job of the same
parcel, index and day. A ``/composites/`` folder, or a job with a window, makes it ``s2-composite`` (dated by the
window end). No scene found: that index is written without scene attributes, never guessed. A legacy index
whose job was deleted is not resurrected; one with no job at all is converted and counted (``no_job``).
One entity per family and day: when two scenes (or windows) share it, nothing of it
is converted: a legacy value is never hidden except by a deleted job. Such a family-day (two scenes or windows
in one day, or an index that cannot be placed: ``ambiguous_take``, ``composite_no_window``) is left untouched,
counted in ``left_legacy`` and listed, for the owner to decide before the EOProduct is removed.

Writing never degrades what is stored: a new entity is created whole; an existing one of the SAME take
(same ``productID``) only receives the whole indices (and other names) it lacks, by sending the full
``analytics`` list and nothing else; an existing one of another take is left and counted (``other_take``).
A second run writes nothing. Nothing is ever deleted here.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid as uuidlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from nkz_platform_sdk import parse_eo_entities

from app.services import eo_satimg_optical as so
from app.services import eo_satimg_radar_lst as rl
from app.services import fiware_integration as fi
from app.services.eo_satellite_imagery import (
    INDEX_NAMES, Family, RasterMeta, SceneMeta, Sensing, Target, _check_scope, _classify, build_entity,
    entity_id as new_entity_id, format_value,
)

logger = logging.getLogger(__name__)

SDM = "https://smartdatamodels.org/dataModel.SatelliteImagery/"
EOPRODUCT_TYPE = SDM + "EOProduct"  # full IRI: a short type without Link expands to the default vocabulary
EOSATIMG_TYPE = SDM + "EOSatelliteImagery"
COUNT_HEADER = "NGSILD-Results-Count"
PAGE = 1000

REVIEW_ENV = "EO_SATIMG_REVIEW_TENANTS"
WINDOW_START, WINDOW_END = dtime(1, 15), dtime(3, 0)  # UTC, end excluded

EXIT_OK, EXIT_ERRORS, EXIT_REFUSED = 0, 1, 2

# Values that belong to a whole raster product: stored, merged and compared as a group.
RASTER_BASES = frozenset(INDEX_NAMES) | {"lst", "backscatterVV", "backscatterVH", "soilMoistureIndex"}
PRIMARY_EXTRA = frozenset({"changeFlag", "changeConfidence", "deltaVV"})  # compared by verify as well
OPTICAL = (Family.S2_INDICES.value, Family.S2_COMPOSITE.value)
_SCENE_FOLDER = re.compile(r"/scenes/([^/]+)/")
_COMPOSITE_FOLDER = re.compile(r"/composites/(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})/")
_UUID_TEXT = re.compile(r"^[0-9a-fA-F-]{32,36}$")


class ReadError(Exception):
    """The read of a tenant is not trustworthy: nothing is written for it."""


class WindowClosed(Exception):
    """The nightly window opened while a tenant was being written."""


# ── data the job reads besides the broker ───────────────────────────────────


@dataclass(frozen=True)
class JobRow:
    """A completed ``calculate_index`` job, deleted ones included (a deleted job is told from no job)."""

    id: str
    index_type: str  # lowercase: ``ndvi``, ``sar-vv`` ...
    deleted: bool
    result: Mapping[str, Any]
    parameters: Mapping[str, Any]
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    geometry: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class SceneRow:
    scene_id: str  # the catalogue (STAC) id
    sensing_date: date
    acquisition_datetime: Optional[datetime]
    footprint: Any
    cloud_coverage: Any = None


class SqlSources:
    """Jobs and scenes from the database; every query runs in its own savepoint (a failure leaves the session usable)."""

    def __init__(self, db):
        self.db = db

    def _set_tenant(self, tenant: str) -> None:
        from sqlalchemy import text

        self.db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant})

    def jobs(self, tenant: str, parcel: str) -> list:
        from app.models.jobs import VegetationJob

        with self.db.begin_nested():
            self._set_tenant(tenant)
            rows = self.db.query(VegetationJob).filter(
                VegetationJob.tenant_id == tenant,
                VegetationJob.entity_id == parcel,
                VegetationJob.job_type == "calculate_index",
                VegetationJob.status == "completed",
            ).all()
        out = []
        for job in rows:
            result = job.result if isinstance(job.result, Mapping) else {}
            params = job.parameters if isinstance(job.parameters, Mapping) else {}
            geometry = None
            # ASSUMPTION: parameters.bounds is the parcel polygon the analysis cropped to (window_crop,
            # reconcile_eo_from_jobs use it the same way); the unused ``bounds`` column is not read.
            for candidate in (params.get("bounds"), result.get("geometry")):
                if isinstance(candidate, Mapping) and "coordinates" in candidate:
                    geometry = dict(candidate)
                    break
            out.append(JobRow(
                id=str(job.id), index_type=str(result.get("index_type") or params.get("index_type") or "").lower(),
                deleted=job.deleted_at is not None, result=result, parameters=params,
                start_date=job.start_date, end_date=job.end_date, geometry=geometry))
        return out

    def scene(self, tenant: str, scene_uuid: str) -> Optional[SceneRow]:
        from sqlalchemy import func

        from app.models import VegetationScene

        with self.db.begin_nested():
            self._set_tenant(tenant)
            row = self.db.query(
                VegetationScene.scene_id, VegetationScene.sensing_date, VegetationScene.acquisition_datetime,
                VegetationScene.cloud_coverage, func.ST_AsGeoJSON(VegetationScene.footprint),
            ).filter(VegetationScene.tenant_id == tenant, VegetationScene.id == uuidlib.UUID(scene_uuid)).first()
        if row is None:
            return None
        return SceneRow(scene_id=row[0], sensing_date=row[1], acquisition_datetime=row[2], cloud_coverage=row[3],
                        footprint=row[4])

    def parcel_geometry(self, tenant: str, parcel: str) -> Optional[dict]:
        """The parcel's location in the broker, or None (404 or no location). Other errors propagate."""
        try:
            entity = fi._with_retry(lambda: fi.SyncOrionClient(tenant).get_entity(parcel), "AgriParcel read")
        except Exception as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                return None
            raise
        location = fi._property_value(fi._attr_by_name(entity or {}, "location"))
        return location if isinstance(location, dict) and "coordinates" in location else None


# ── reading the broker ──────────────────────────────────────────────────────


def read_all(client, type_iri: str) -> list:
    """Every entity of a type, without a Link header, paged, cross-checked against the broker's count.

    A mismatch (or a missing count) raises ``ReadError``; transient errors are retried by ``fi._with_retry``.
    """
    headers = client._headers("none")  # tenant headers only: no Link, no Content-Type
    entities, offset, expected = [], 0, None
    while True:
        params = {"type": type_iri, "limit": PAGE, "offset": offset, "count": "true"}

        def fetch(params=params):
            resp = client._session.get(client._url("/ngsi-ld/v1/entities"), params=params, headers=headers,
                                       timeout=getattr(client, "timeout", 30))
            resp.raise_for_status()
            return resp

        resp = fi._with_retry(fetch, "EO read")
        try:
            count = int((getattr(resp, "headers", None) or {}).get(COUNT_HEADER))
        except (TypeError, ValueError):
            raise ReadError(f"missing or invalid {COUNT_HEADER} header")
        if expected is None:
            expected = count
        elif count != expected:
            raise ReadError(f"the broker's count changed while reading: {expected} then {count}")
        page = resp.json()
        entities.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    if len(entities) != expected:
        raise ReadError(f"read {len(entities)} entities but the broker counts {expected}")
    return entities


# ── the plan ────────────────────────────────────────────────────────────────


@dataclass
class Item:
    """One new entity the legacy data yields (before the broker's own state is looked at)."""

    legacy_id: str
    parcel: str
    family: Family
    day: date
    scene: Optional[SceneMeta]
    sensing: Sensing
    values: dict
    description: str
    geometry: Optional[Mapping[str, Any]]
    raster: Optional[RasterMeta]
    entity: dict  # the whole entity as it would be created


@dataclass
class Plan:
    items: list = field(default_factory=list)
    counters: Counter = field(default_factory=Counter)
    by_family: dict = field(default_factory=lambda: defaultdict(Counter))
    unconvertible: list = field(default_factory=list)  # {"legacy_id", "family", "day", "reason"} (tenant removed)
    dropped: list = field(default_factory=list)  # {"legacy_id", "family", "day", "indices"}


def _anon(entity_id: str, tenant: str) -> str:
    return entity_id.replace(f":{tenant}:", ":<tenant>:")


def _allowed(name: str, family: Family) -> bool:
    try:
        _classify(name)
        _check_scope(name, family, "parcel")
        return True
    except ValueError:
        return False


def _day_of(value: Any) -> Optional[date]:
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _job_window(job: JobRow) -> Optional[tuple]:
    """The window of a composite job: its result's ``date_range`` or its date columns (never parameters)."""
    window = job.result.get("date_range")
    if isinstance(window, Mapping):
        start, end = _day_of(window.get("start")), _day_of(window.get("end"))
        if start and end:
            return start, end
    if job.start_date and job.end_date:
        return job.start_date, job.end_date
    return None


def _is_composite_job(job: JobRow) -> bool:
    return bool(job.result.get("is_composite")) or _job_window(job) is not None


def _mean6(job: JobRow) -> Optional[float]:
    stats = job.result.get("statistics")
    mean = stats.get("mean") if isinstance(stats, Mapping) else None
    return round(float(mean), 6) if isinstance(mean, (int, float)) and not isinstance(mean, bool) else None


def _key_of(url: Optional[str]) -> Optional[tuple]:
    """``(bucket, key)`` of an ``s3://bucket/key`` URL."""
    if isinstance(url, str) and url.startswith("s3://") and "/" in url[5:]:
        bucket, _, key = url[5:].partition("/")
        return bucket, key
    return None


class Planner:
    def __init__(self, tenant: str, src, plan: Plan):
        self.tenant, self.src, self.plan = tenant, src, plan
        self._jobs: dict = {}
        self._scenes: dict = {}
        self._parcels: dict = {}

    # lookups, cached for the run
    def jobs(self, parcel: str) -> list:
        if parcel not in self._jobs:
            self._jobs[parcel] = self.src.jobs(self.tenant, parcel)
        return self._jobs[parcel]

    def scene(self, scene_uuid: Optional[str]) -> Optional[SceneRow]:
        if not scene_uuid or not _UUID_TEXT.match(str(scene_uuid)):
            return None
        if scene_uuid not in self._scenes:
            self._scenes[scene_uuid] = self.src.scene(self.tenant, str(scene_uuid))
        return self._scenes[scene_uuid]

    def parcel_geometry(self, parcel: str) -> Optional[Mapping]:
        if parcel not in self._parcels:
            self._parcels[parcel] = self.src.parcel_geometry(self.tenant, parcel)
        return self._parcels[parcel]

    def geometry(self, parcel: str, jobs: Sequence[JobRow]) -> Optional[Mapping]:
        """What the analysis used (a live job's), else the parcel's own location; never invented.

        ASSUMPTION: the parcel's current location in the broker is an acceptable fallback when no job kept the geometry
        it used (the brief's rule names the analysis' geometry or the zone's; zones are out of scope). Radar and
        surface temperature have no such job (the SAR download job is not a ``calculate_index`` job), so they always
        take the parcel's location.
        """
        for job in jobs:
            if job.geometry and not job.deleted:
                return job.geometry
        return self.parcel_geometry(parcel)

    # one legacy entity
    def entity(self, raw: Mapping[str, Any]) -> None:
        plan, tenant = self.plan, self.tenant
        plan.counters["old"] += 1
        legacy_id = str(raw.get("id", ""))
        observations = parse_eo_entities([raw])
        if not observations:
            plan.counters["shells"] += 1
            return
        if observations[0].target == "zone":
            plan.counters["zones_skipped"] += 1
            return
        parcel = observations[0].parcel_id
        for obs in observations:
            plan.by_family[obs.family]["old"] += 1
        if not parcel:
            plan.counters["no_parcel"] += 1
            return
        day = observations[0].sensing_day
        optical = [o for o in observations if o.family in OPTICAL]
        if optical:
            self._optical(legacy_id, parcel, day, optical)
        for obs in observations:
            if obs.family in (Family.S1_BACKSCATTER.value, Family.S1_SOIL_MOISTURE.value, Family.S1_CHANGE.value):
                self._radar(legacy_id, parcel, obs)
            elif obs.family in (Family.LANDSAT_LST.value, Family.CLMS_LST.value):
                self._lst(legacy_id, parcel, obs)

    # shared: build the candidate entity or say why not
    def _add(self, legacy_id, parcel, family, take_scene, sensing, values, description, geometry, raster) -> None:
        plan = self.plan
        values = {n: v for n, v in values.items() if _allowed(n, family)}
        wide_zero = values.get("pixelCount") == 0
        if wide_zero:
            values.pop("pixelCount")
        target = Target("parcel", parcel, None, None, so._polygon(geometry))
        entity = None if wide_zero else build_entity(
            tenant=self.tenant, target=target, family=family, sensing=sensing, analytics=values, scene=take_scene,
            raster=raster, result_description=description + ("" if target.geometry else so.NO_GEOMETRY))
        day = sensing.day
        if entity is None:
            reason = "no_valid_pixels" if wide_zero else "fewer_than_2_analytics"
            plan.counters["unconvertible"] += 1
            plan.by_family[family.value]["unconvertible"] += 1
            plan.unconvertible.append({"legacy_id": _anon(legacy_id, self.tenant), "family": family.value,
                                       "day": day.isoformat(), "reason": reason})
            return
        if target.geometry is None:
            plan.counters["no_geometry"] += 1
        plan.counters["items"] += 1
        plan.by_family[family.value]["items"] += 1
        plan.items.append(Item(legacy_id, parcel, family, day, take_scene, sensing, values, description,
                               target.geometry, raster, entity))

    # ── optical ─────────────────────────────────────────────────────────────

    def _optical(self, legacy_id: str, parcel: str, day: date, observations: list) -> None:
        plan = self.plan
        self._deleted_dropped = 0
        self._blocked = []  # (index, reason): indices that cannot be placed for a reason other than a deleted job
        records: dict = {}
        wide: dict = {}
        for obs in sorted(observations, key=lambda o: o.family != Family.S2_COMPOSITE.value):  # indices last: they win
            for name, value in obs.values.items():
                base = name.split(".", 1)[0]
                if base in INDEX_NAMES:
                    rec = records.setdefault(base, {"values": {}, "url": obs.legacy_raster_urls.get(base)})
                    rec["values"][name] = value
                else:
                    wide[name] = value
        jobs = self.jobs(parcel)
        # ASSUMPTION: "no scene" is a take of its own (key None): it never mixes with a resolved scene's indices.
        groups: dict = {}  # (family, entity day, take key) -> dict
        for idx in INDEX_NAMES:
            rec = records.get(idx)
            if rec is None:
                continue
            resolved = self._resolve_index(idx, rec, parcel, day, jobs)
            if resolved is None:
                continue
            key = resolved["key"]
            group = groups.get(key)
            if group is None:
                group = groups[key] = {**resolved, "jobs": list(resolved["jobs"]), "values": {}, "urls": {}}
            else:
                group["jobs"].extend(j for j in resolved["jobs"] if j not in group["jobs"])
            group["values"].update(rec["values"])
            group["urls"][idx] = rec["url"]
            group["stat_api"] = group.get("stat_api", True) and resolved.get("stat_api", False)
        by_entity: dict = defaultdict(list)
        for key, group in groups.items():
            by_entity[(key[0], key[1])].append(group)

        def leave(family_value, entity_day, indices, reason):
            # A legacy value is never hidden (a deleted job aside): the family-day is left as it is, for the owner.
            plan.counters["left_legacy"] += 1
            plan.dropped.append({"legacy_id": _anon(legacy_id, self.tenant), "family": family_value,
                                 "day": entity_day.isoformat() if entity_day else None, "reason": reason,
                                 "indices": sorted(indices)})

        if self._blocked:
            blocked = {i for i, _ in self._blocked}
            reason = self._blocked[0][1]
            if not by_entity:
                leave(Family.S2_INDICES.value, None, blocked, reason)
            for (family_value, entity_day), candidates in sorted(by_entity.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                leave(family_value, entity_day, blocked | {i for g in candidates for i in g["values"] if i in INDEX_NAMES},
                      reason)
            return
        for (family_value, entity_day), candidates in sorted(by_entity.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            family = Family(family_value)
            if len(candidates) > 1:  # two scenes or windows share the family-day: converting would hide one
                leave(family_value, entity_day, {i for g in candidates for i in g["values"] if i in INDEX_NAMES},
                      "two_scenes" if family is Family.S2_INDICES else "two_windows")
                continue
            winner = candidates[0]
            values = dict(winner["values"])
            row = winner.get("scene_row")
            # Take-wide legacy values belong to a take only when the legacy entity had exactly one and no index was
            # hidden by a deleted job (the count or cloud cover may be the hidden one's); else they are omitted.
            attributable = len(groups) == 1 and not self._deleted_dropped
            if attributable:
                values.update(wide)
            elif wide:
                plan.counters["wide_omitted"] += 1
            scene_cloud = so.cloud_coverage(row.cloud_coverage) if row is not None else None
            if scene_cloud is not None and family is Family.S2_INDICES:  # never a scene's cloud cover on a composite
                if "cloudCoverage" not in values:
                    values["cloudCoverage"] = scene_cloud
                elif abs(float(values["cloudCoverage"]) - scene_cloud) > 0.005:
                    plan.counters["cloud_differs"] += 1  # the legacy value is kept
            if family is Family.S2_COMPOSITE:
                start, end = winner["window"]
                folder = self._folder(winner)
                take = so.composite_take(start, end, folder)
            elif row is not None:
                folder = self._folder(winner)
                take = so.scene_take(row, folder)
            elif winner["stat_api"]:
                folder = None
                take = so.no_scene_take(entity_day)
            else:
                folder = None  # the scene could not be resolved: say so, never claim the Statistical API
                take = so.OpticalTake(Family.S2_INDICES, Sensing(entity_day, None), None,
                                      "Zonal vegetation indices of the day from Sentinel-2 L2A; the scene is not identified.")
            raster = RasterMeta(folder, [(n, so._EXPLANATION[n]) for n in INDEX_NAMES if n in values]) if folder else None
            self._add(legacy_id, parcel, family, take.scene, take.sensing, values, take.description,
                      self.geometry(parcel, winner["jobs"]), raster)

    def _matching_jobs(self, idx: str, rec: dict, key: Optional[str], day: date, jobs: Sequence[JobRow]) -> list:
        """The jobs this legacy index can come from: its raster's job alone; else the day's, narrowed by value."""
        candidates = [j for j in jobs if j.index_type == idx and not so.job_wrote_nothing(j.result)]
        by_path = [j for j in candidates if key is not None and j.result.get("raster_path") == key]
        if by_path:
            return by_path  # the raster names its job: it alone decides deleted against live
        same_day = [j for j in candidates if _day_of(j.result.get("sensing_date")) == day]
        mean = rec["values"].get(idx)
        if len(same_day) > 1 and isinstance(mean, (int, float)):
            exact = [j for j in same_day if _mean6(j) == round(float(mean), 6)]
            if exact:
                same_day = exact
        return same_day

    def _folder(self, group: dict) -> Optional[str]:
        """The raster folder when EVERY kept index resolves to the same one, else none."""
        folders = set()
        for idx, url in group["urls"].items():
            if idx not in group["values"]:
                continue
            parsed = _key_of(url)
            folders.add(so.raster_folder(parsed[0], parsed[1], idx) if parsed else None)
        return next(iter(folders)) if len(folders) == 1 and None not in folders else None

    def _resolve_index(self, idx: str, rec: dict, parcel: str, day: date, jobs: Sequence[JobRow]) -> Optional[dict]:
        """Family, entity day, scene and window of ONE legacy index; None when it is not converted (counted)."""
        plan = self.plan
        url = rec["url"]
        parsed = _key_of(url)
        key = parsed[1] if parsed else None
        matching = self._matching_jobs(idx, rec, key, day, jobs)
        live = [j for j in matching if not j.deleted]
        if matching and not live:
            plan.counters["job_deleted"] += 1
            self._deleted_dropped += 1
            return None
        if not matching:
            plan.counters["no_job"] += 1
        takes = {("window", _job_window(j)) if _is_composite_job(j) else ("scene", str(j.result.get("scene_id") or ""))
                 for j in live}
        if len(takes) > 1:
            if any(t[0] == "window" for t in takes):
                plan.counters["ambiguous_take"] += 1  # a composite and another take fit: never guessed
                self._blocked.append((idx, "ambiguous_take"))
                return None
            plan.counters["ambiguous_scene"] += 1
            live = []  # several scenes fit and nothing tells which: no scene, as the rule says
        in_composite_folder = bool(url) and "/composites/" in url
        in_scene_folder = bool(url) and _SCENE_FOLDER.search(url) is not None
        composite_job = next((j for j in live if _is_composite_job(j)), None)
        if in_composite_folder or (not in_scene_folder and composite_job is not None):
            window = _job_window(composite_job) if composite_job else None
            if window is None and url:
                found = _COMPOSITE_FOLDER.search(url)
                window = (date.fromisoformat(found.group(1)), date.fromisoformat(found.group(2))) if found else None
            if window is None:
                plan.counters["composite_no_window"] += 1
                self._blocked.append((idx, "composite_no_window"))
                return None
            return {"key": (Family.S2_COMPOSITE.value, window[1], window), "window": window, "jobs": live}
        # single scene: the raster folder first, else the job
        scene_uuid = None
        if url and (found := _SCENE_FOLDER.search(url)):
            scene_uuid = found.group(1)
        else:
            job = next((j for j in live if j.result.get("scene_id")), None)
            scene_uuid = str(job.result["scene_id"]) if job else None
        # The Statistical API is only claimed when a live job exists and holds no scene at all.
        stat_api = bool(live) and not in_scene_folder and all(not j.result.get("scene_id") for j in live)
        row = self.scene(scene_uuid)
        if row is not None and row.sensing_date != day:
            plan.counters["scene_day_mismatch"] += 1  # never guessed: this index goes without a scene
            row = None
        if row is None:
            plan.counters["no_scene"] += 1
        return {"key": (Family.S2_INDICES.value, day, row.scene_id if row else None), "scene_row": row, "jobs": live,
                "stat_api": stat_api and row is None}

    # ── radar ───────────────────────────────────────────────────────────────

    def _sar_scene(self, parcel: str, day: date, stored_id: Optional[str]) -> tuple:
        """(scene id or None, job rows, deleted-only flag) of the day's Sentinel-1 pass."""
        # ASSUMPTION: the deleted-job rule (not resurrected) extends to radar: a day whose SAR jobs are all deleted is
        # not converted, for the three radar families alike. Two distinct live passes the same day: no scene.
        sar = [j for j in self.jobs(parcel) if j.index_type in ("sar-vv", "sar-vh")
               and _day_of(j.result.get("sensing_date")) == day]
        live = [j for j in sar if not j.deleted]
        if sar and not live:
            return None, [], True
        ids = sorted({str(j.parameters["scene_id"]) for j in live if j.parameters.get("scene_id")})
        if stored_id:
            return stored_id, live, False
        if len(ids) > 1:
            self.plan.counters["ambiguous_scene"] += 1
            return None, live, False
        return (ids[0] if ids else None), live, False

    def _radar(self, legacy_id: str, parcel: str, obs) -> None:
        plan = self.plan
        family = Family(obs.family)
        stored = obs.product_id if family is not Family.S1_BACKSCATTER else None
        scene_id, live, deleted = self._sar_scene(parcel, obs.sensing_day, stored)
        if deleted:
            plan.counters["job_deleted"] += 1
            return
        if scene_id is None:
            plan.counters["no_job" if not live else "no_scene"] += 1
        take = rl.radar_take(scene_id, {"sensing_date": obs.sensing_day.isoformat()}) if scene_id else None
        description = {Family.S1_BACKSCATTER: rl._DESC_BACKSCATTER, Family.S1_SOIL_MOISTURE: rl._DESC_SOIL_MOISTURE,
                       Family.S1_CHANGE: rl._DESC_CHANGE}[family]
        self._add(legacy_id, parcel, family, take.scene if take else None,
                  take.sensing if take else Sensing(obs.sensing_day, None), dict(obs.values), description,
                  self.parcel_geometry(parcel), None)

    # ── surface temperature ─────────────────────────────────────────────────

    def _lst(self, legacy_id: str, parcel: str, obs) -> None:
        family = Family(obs.family)
        scene_id = (obs.product_id or "")
        if scene_id.startswith("CLMS:"):
            scene_id = scene_id[len("CLMS:"):]  # the live writer stores the raw catalogue id
        item = {"id": scene_id, "properties": {"datetime": obs.sensing_day.isoformat()}, "geometry": None}
        make = rl.clms_take if family is Family.CLMS_LST else rl.landsat_take
        take = make(item) if scene_id and scene_id != "unknown" else None
        description = rl._DESC_CLMS if family is Family.CLMS_LST else rl._DESC_LANDSAT
        if take is None:
            self.plan.counters["no_scene"] += 1
        self._add(legacy_id, parcel, family, take.scene if take else None,
                  take.sensing if take else Sensing(obs.sensing_day, None), dict(obs.values), description,
                  self.parcel_geometry(parcel), None)


def plan_tenant(tenant: str, entities: Sequence[Mapping[str, Any]], src) -> Plan:
    """Every legacy entity lands in exactly one bucket (counted); a failing one is counted and the run goes on."""
    plan = Plan()
    planner = Planner(tenant, src, plan)
    for raw in entities:
        try:
            planner.entity(raw)
        except Exception as exc:
            plan.counters["errors"] += 1
            logger.error("eo_satimg_backfill: entity %s not planned: %s", _anon(str(raw.get("id")), tenant), exc,
                         exc_info=True)
    return plan


# ── applying ────────────────────────────────────────────────────────────────


def _same_take(raw, obs, item: Item) -> bool:
    if item.family is Family.S2_COMPOSITE:
        return so._description(raw).startswith(item.description)
    return obs.product_id == (item.scene.product_id if item.scene else None)


def _group_of(name: str) -> str:
    base = name.split(".", 1)[0]
    return base if base in RASTER_BASES else name


def _stored_item_count(raw) -> int:
    for key, attr in raw.items():
        if key.rsplit("/", 1)[-1] == "analytics":
            value = attr.get("value") if isinstance(attr, Mapping) else attr
            return len(value) if isinstance(value, list) else (0 if value is None else 1)
    return 0


def write_item(tenant: str, item: Item, apply: bool) -> str:
    """created / merged / unchanged / other_take / unreadable_existing / error; writes only with ``apply``."""
    eid = item.entity["id"]
    raw = fi.get_eo_satellite_imagery(tenant, eid)
    if raw is None:
        if apply and fi.upsert_eo_satellite_imagery(tenant, item.entity) is None:
            return "error"
        return "created"
    found = parse_eo_entities([raw])
    obs = found[0] if found else None
    if obs is None or _stored_item_count(raw) != len(obs.values):
        return "unreadable_existing"  # something stored could not be read: rewriting the list would lose it
    if not _same_take(raw, obs, item):
        return "other_take"
    held = {_group_of(n) for n in obs.values}
    added = {n: v for n, v in item.values.items() if _group_of(n) not in held and n not in obs.values}
    if not added:
        return "unchanged"
    if apply:
        merged = {**obs.values, **added}
        sensing = Sensing(item.day, obs.sensing_time or item.sensing.time_utc)
        target = Target("parcel", item.parcel, None, None, item.geometry)
        entity = build_entity(tenant=tenant, target=target, family=item.family, sensing=sensing, analytics=merged,
                              scene=item.scene, raster=None, result_description=item.description)
        if entity is None:
            return "error"
        # only the full list: stored time, description, geometry and product attributes are never touched
        body = {"id": eid, "type": entity["type"], "analytics": entity["analytics"]}
        if fi.upsert_eo_satellite_imagery(tenant, body) is None:
            return "error"
    return "merged"


def execute(tenant: str, plan: Plan, apply: bool, guard: Callable[[], None]) -> None:
    for item in plan.items:
        guard()
        try:
            outcome = write_item(tenant, item, apply)
        except WindowClosed:
            raise
        except Exception as exc:
            outcome = "error"
            logger.error("eo_satimg_backfill: %s not written: %s", _anon(item.entity["id"], tenant), exc,
                         exc_info=True)
        plan.counters[outcome if outcome != "error" else "errors"] += 1
        plan.by_family[item.family.value][outcome] += 1


# ── verify ──────────────────────────────────────────────────────────────────


def verify_tenant(tenant: str, plan: Plan, new_entities: Sequence[Mapping[str, Any]], accept_other_take: bool = False) -> dict:
    """Old against new per family, and per parcel-day the index names and values at the contract's decimals."""
    raw_by_id = {str(e.get("id")): e for e in new_entities}
    new = {}
    per_family_new: Counter = Counter()
    for obs in parse_eo_entities(new_entities):
        if obs.shape == "sdm" and obs.target == "parcel":
            new[(obs.parcel_id, obs.family, obs.sensing_day)] = obs
            per_family_new[obs.family] += 1
    diffs, matched, planned = [], Counter(), set()
    skipped: dict = defaultdict(Counter)
    for item in plan.items:
        obs = new.get((item.parcel, item.family.value, item.day))
        label = {"legacy_id": _anon(item.legacy_id, tenant), "family": item.family.value, "day": item.day.isoformat()}
        if obs is None:
            diffs.append({**label, "kind": "missing_entity"})
            continue
        planned.add((item.parcel, item.family.value, item.day))
        if not _same_take(raw_by_id[obs.entity_id], obs, item):
            skipped[item.family.value]["other_take"] += 1
            if not accept_other_take:
                composite = item.family is Family.S2_COMPOSITE
                diffs.append({**label, "kind": "other_take",
                              "expected": item.description if composite else (item.scene.product_id if item.scene else None),
                              "found": so._description(raw_by_id[obs.entity_id]) if composite else obs.product_id})
            continue
        bad = False
        for name in sorted(item.values):  # EVERY name: statistics, interior, counts and quality values too
            if name not in obs.values:
                diffs.append({**label, "kind": "missing_index" if name in RASTER_BASES else "missing_value", "name": name})
                bad = True
            elif format_value(name, item.values[name]) != format_value(name, obs.values[name]):
                diffs.append({**label, "kind": "value_differs", "name": name, "old": format_value(name, item.values[name]),
                              "new": format_value(name, obs.values[name])})
                bad = True
        if not bad:
            matched[item.family.value] += 1
    families = {}
    for family in sorted(set(plan.by_family) | set(per_family_new)):
        counts = plan.by_family.get(family, Counter())
        reasons = Counter(skipped.get(family, Counter()))
        if counts.get("unconvertible"):
            reasons["unconvertible"] = counts["unconvertible"]
        families[family] = {"old": counts.get("old", 0), "new": per_family_new.get(family, 0),
                            "converted": matched.get(family, 0), "skipped": dict(reasons)}
    unplanned = [{"entity": _anon(o.entity_id, tenant), "family": k[1], "day": k[2].isoformat()}
                 for k, o in sorted(new.items(), key=lambda kv: kv[1].entity_id) if k not in planned]
    return {"families": families, "skipped_total": dict(plan.counters), "diffs": diffs, "unplanned": unplanned}


# ── command line ────────────────────────────────────────────────────────────


def in_nightly_window(now: datetime) -> bool:
    moment = now.astimezone(timezone.utc).time()
    return WINDOW_START <= moment < WINDOW_END


def _review_tenants() -> Optional[set]:
    # ASSUMPTION: the review tenant cannot be named in public code, so the guard reads it from the environment of
    # the deployment; ``--apply`` refuses to run when the variable is absent (an empty value is allowed).
    raw = os.environ.get(REVIEW_ENV)
    return None if raw is None else {t.strip() for t in raw.split(",") if t.strip()}


def _default_client(tenant: str):
    return fi.SyncOrionClient(tenant)


def _line(label: str, mode: str, plan: Plan) -> str:
    counts = " ".join(f"{k}={v}" for k, v in sorted(plan.counters.items()))
    return f"{label} {mode} {counts}"


def main(argv=None, db=None, sources=None, client_factory: Optional[Callable] = None,
         clock: Optional[Callable[[], datetime]] = None, out=None) -> int:
    parser = argparse.ArgumentParser(description="Convert parcel EOProduct entities into EOSatelliteImagery.")
    parser.add_argument("--tenant", action="append", required=True, help="a tenant (repeatable; no discovery)")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    parser.add_argument("--verify", action="store_true", help="compare old and new per tenant and family")
    parser.add_argument("--accept-other-take", action="store_true",
                        help="--verify: an entity holding another take is a counted reason, not a failure")
    parser.add_argument("--allow-review", action="store_true", help="allow a tenant listed in " + REVIEW_ENV)
    parser.add_argument("--json", action="store_true", help="one JSON object per tenant instead of text")
    args = parser.parse_args(argv)
    out = out or sys.stdout
    clock = clock or (lambda: datetime.now(timezone.utc))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.apply and args.verify:
        parser.error("--apply and --verify are separate runs")
    review = _review_tenants()
    if args.apply and review is None:
        out.write(f"REFUSED: set {REVIEW_ENV} (it may be empty) before --apply\n")
        return EXIT_REFUSED
    if args.apply and in_nightly_window(clock()):
        out.write("REFUSED: --apply must not run between 01:15 and 03:00 UTC (the vegetation passes)\n")
        return EXIT_REFUSED
    if args.apply:
        out.write("WARNING: do not leave this running into 01:15-03:00 UTC; Celery tasks are not inspected here\n")
    if review and not args.allow_review and any(t in review for t in args.tenant):
        out.write("REFUSED: a requested tenant needs --allow-review\n")
        return EXIT_REFUSED

    own_db = sources is None
    if own_db:
        from app import database  # imported late: needs DATABASE_URL, which tests do not set

        db = database.SessionLocal()
        sources = SqlSources(db)
    factory = client_factory or _default_client

    def guard() -> None:
        if args.apply and in_nightly_window(clock()):
            raise WindowClosed()

    code = EXIT_OK
    try:
        for number, tenant in enumerate(args.tenant, 1):
            label = f"tenant#{number}"
            try:
                guard()
                client = factory(tenant)
                plan = plan_tenant(tenant, read_all(client, EOPRODUCT_TYPE), sources)
                report: dict = {"tenant": label, "mode": "apply" if args.apply else "verify" if args.verify else "dry-run"}
                if args.verify:
                    result = verify_tenant(tenant, plan, read_all(client, EOSATIMG_TYPE), args.accept_other_take)
                    report.update(result)
                    if result["diffs"] or plan.counters.get("errors"):
                        code = max(code, EXIT_ERRORS)
                else:
                    execute(tenant, plan, args.apply, guard)
                    report.update({"counters": dict(plan.counters),
                                   "families": {f: dict(c) for f, c in sorted(plan.by_family.items())},
                                   "unconvertible": plan.unconvertible, "left_legacy": plan.dropped})
                    if plan.counters.get("errors"):
                        code = max(code, EXIT_ERRORS)
            except WindowClosed:
                out.write(f"{label} STOPPED: the nightly window opened\n")
                return EXIT_REFUSED
            except Exception as exc:
                logger.error("eo_satimg_backfill: %s aborted: %s", label, exc, exc_info=True)
                out.write(f"{label} ABORTED {type(exc).__name__}: nothing more was written for it\n")
                code = max(code, EXIT_ERRORS)
                continue
            if args.json:
                out.write(json.dumps(report, sort_keys=True, default=str) + "\n")
            elif args.verify:
                for family, row in report["families"].items():
                    out.write(f"{label} verify family={family} old={row['old']} new={row['new']} "
                              f"converted={row['converted']} skipped={json.dumps(row['skipped'], sort_keys=True)}\n")
                for diff in report["diffs"]:
                    out.write(f"{label} DIFF {json.dumps(diff, sort_keys=True)}\n")
                totals = " ".join(f"{k}={v}" for k, v in sorted(report["skipped_total"].items()))
                out.write(f"{label} verify totals {totals}\n")
                out.write(f"{label} verify unplanned={len(report['unplanned'])}\n")
                for entry in report["unplanned"]:
                    out.write(f"{label} UNPLANNED {json.dumps(entry, sort_keys=True)}\n")
                out.write(f"{label} verify diffs={len(report['diffs'])}\n")
            else:
                out.write(_line(label, report["mode"], plan) + "\n")
                for entry in plan.unconvertible:
                    out.write(f"{label} unconvertible {json.dumps(entry, sort_keys=True)}\n")
        return code
    finally:
        if own_db:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
