"""Orchestration of a multi-date zoning proposal for one parcel."""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Literal

import numpy as np

from app.jobs.zoning_multidate import config as cfg
from app.jobs.zoning_multidate.dates import RasterRow, load_raster_rows, select_dates
from app.jobs.zoning_multidate.grid import parcel_grid, read_onto_grid
from app.jobs.zoning_multidate.levels import classify, usable_dates
from app.jobs.zoning_multidate.polygons import ZoneSummary, level_polygons, summarize
from app.jobs.zoning_multidate.publish import build_entities, publish, retire_automatic_zonings
from app.jobs.zoning_multidate.regions import regularize

logger = logging.getLogger(__name__)


@dataclass
class Sources:
    """Where the proposal reads from; injectable so the pipeline is testable."""
    rows: Callable[[str], list[RasterRow]]
    raster_bytes: Callable[[RasterRow], bytes | None]  # materialises pending rasters; None on failure
    parcel_geometry: Callable[[], dict]
    crop_species: Callable[[], str | None]


@dataclass
class ProposalResult:
    status: Literal["zones", "no_zones"]
    reason: str | None
    index: str
    run_id: str | None = None
    zones: list[ZoneSummary] = field(default_factory=list)
    dates_used: list[date] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    parcel_area_ha: float = 0.0
    features: list[dict] = field(default_factory=list)


def choose_index(requested: str | None, crop_species: str | None) -> str:
    """The requested index, or the first zonable default for the parcel's crop."""
    if requested:
        if requested not in cfg.ZONABLE_INDICES:
            raise ValueError(f"index {requested} cannot be used for zoning")
        return requested
    from app.api.scenes import default_indices_for_species
    for index in default_indices_for_species(crop_species):
        if index in cfg.ZONABLE_INDICES:
            return index
    return "NDVI"


def validate_zoning_index(index: str | None) -> str | None:
    """API-side check: an index the proposal cannot use is a 422, not a failed job later."""
    if index and index not in cfg.ZONABLE_INDICES:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=f"Index {index} cannot be used for zoning; "
                            f"use one of {sorted(cfg.ZONABLE_INDICES)} or leave it empty")
    return index or None


def _no_zones(result: ProposalResult, reason: str, fiware, parcel_urn: str) -> ProposalResult:
    result.status, result.reason = "no_zones", reason
    retired = retire_automatic_zonings(fiware, parcel_urn, keep_run_id=None)
    logger.info("zoning %s: no zones (%s); %d automatic zones retired", parcel_urn, reason, retired)
    return result


def propose_zoning(sources: Sources, fiware, parcel_urn: str, index: str | None,
                   today: date, now: datetime) -> ProposalResult:
    idx = choose_index(index, None if index else sources.crop_species())
    result = ProposalResult(status="no_zones", reason=None, index=idx)
    grid = parcel_grid(sources.parcel_geometry(), cfg.RES_M)
    result.parcel_area_ha = grid.parcel_utm.area / 10_000.0

    dropped = {"pending_failed": 0, "unreadable": 0}
    arrays: list[np.ndarray] = []
    dates: list[date] = []
    for row in select_dates(sources.rows(idx), today=today, years_back=cfg.YEARS_BACK):
        data = sources.raster_bytes(row)
        if data is None:
            dropped["pending_failed" if row.pending else "unreadable"] += 1
            continue
        try:
            arrays.append(read_onto_grid(data, grid))
            dates.append(row.sensing_date)
        except Exception:
            logger.exception("zoning %s: unreadable raster for %s", parcel_urn, row.sensing_date)
            dropped["unreadable"] += 1
    result.dropped = dropped
    # Fail-safe: when sources fail (storage down, Copernicus quota or credentials), the
    # evidence is incomplete for reasons that say nothing about the field. Neither publish
    # a weaker proposal nor delete the zoning in use; report and let a later run decide.
    failed = dropped["pending_failed"] + dropped["unreadable"]
    if failed and (not arrays or failed / (failed + len(arrays)) > cfg.MAX_FAILED_SHARE):
        result.status, result.reason = "no_zones", "sources_unavailable"
        logger.warning("zoning %s: %d of %d rasters unavailable; current zoning kept",
                       parcel_urn, failed, failed + len(arrays))
        return result
    if not arrays:
        return _no_zones(result, "not_enough_dates", fiware, parcel_urn)

    stack = np.stack(arrays)
    keep, filtered = usable_dates(stack, grid.core, idx)
    dropped.update(filtered)
    if len(keep) < cfg.MIN_DATES:
        if failed:  # a missing date might have made the difference
            result.status, result.reason = "no_zones", "sources_unavailable"
            return result
        return _no_zones(result, "not_enough_dates", fiware, parcel_urn)
    kept = stack[keep]
    result.dates_used = [dates[i] for i in keep]

    levels = classify(kept, grid.core)
    if levels.homogeneous:
        return _no_zones(result, "homogeneous", fiware, parcel_urn)
    regular = regularize(levels.levels, grid.inside, cfg.RES_M, cfg.MIN_ZONE_HA)
    zones = summarize(regular, kept, levels.rel, level_polygons(regular, grid), core=grid.core)
    if len(zones) < 2:
        return _no_zones(result, "single_level", fiware, parcel_urn)

    run_id = uuid.uuid4().hex
    entities = build_entities(parcel_urn, run_id, idx, result.dates_used, zones, grid.crs, now)
    publish(fiware, entities)
    retired = retire_automatic_zonings(fiware, parcel_urn, keep_run_id=run_id)
    logger.info("zoning %s: %d zones published (run %s); %d older automatic zones retired",
                parcel_urn, len(zones), run_id, retired)
    result.status, result.run_id, result.zones = "zones", run_id, zones
    from pyproj import Transformer
    to_wgs84 = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    result.features = []
    for e, z in zip(entities, zones):
        largest = max(getattr(z.geometry_utm, "geoms", [z.geometry_utm]), key=lambda g: g.area)
        anchor = largest.representative_point()
        props = _properties(z)
        props["label_point"] = [round(v, 7) for v in to_wgs84.transform(anchor.x, anchor.y)]
        result.features.append({"type": "Feature", "geometry": e["location"]["value"], "properties": props})
    return result


def _properties(z: ZoneSummary) -> dict:
    return {
        # Legacy keys: exports and older readers of the job geojson.
        "zone_id": z.rank,
        "cluster_id": z.rank - 1,
        "zone_class": z.level,
        "mean_value": round(z.mean_value, 4),
        "area_ha": round(z.area_ha, 2),
        "prescription_rate": round(float(np.clip(1 + (z.mean_value - 0.3) * 0.5, 0.5, 2.0)), 3),
        # Multi-date proposal.
        "zone_level": z.level,
        "relative_pct": z.relative_pct,
        "confirming_dates": z.confirming,
        "total_dates": z.total,
    }


def vra_job_result(result: ProposalResult) -> dict[str, Any]:
    """The VRA_ZONES job result stored in vegetation_jobs.result."""
    return {
        "status": result.status,
        "reason": result.reason,
        "zones_created": len(result.zones),
        "run_id": result.run_id,
        "dates_used": [d.isoformat() for d in result.dates_used],
        "dropped": result.dropped,
        "geojson": {"type": "FeatureCollection", "features": result.features},
        "index_type": "VRA_ZONES",
        "zoning_source": result.index,
    }


def default_sources(db, fiware, tenant_id: str, parcel_urn: str) -> Sources:
    """Production sources: vegetation_jobs, object storage, Copernicus materialisation, Orion."""
    import asyncio

    from app.models import VegetationJob
    from app.services.copernicus_raster import ensure_copernicus_raster
    from app.services.storage import create_storage_service, generate_tenant_bucket_name

    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(tenant_id)
    storage = create_storage_service(storage_type=os.getenv("STORAGE_TYPE", "s3"), default_bucket=bucket)

    def raster_bytes(row: RasterRow) -> bytes | None:
        path = row.raster_path
        try:
            if not path and row.pending:
                job = db.get(VegetationJob, row.job_id)
                path = ensure_copernicus_raster(db, job) if job is not None else None
            if not path:
                return None
            with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
                storage.download_file(path, tmp.name)
                with open(tmp.name, "rb") as fh:
                    return fh.read()
        except Exception:
            logger.exception("zoning %s: raster for %s unavailable", parcel_urn, row.sensing_date)
            return None

    def parcel_geometry() -> dict:
        response = fiware.session.get(
            f"{fiware.context_broker_url}/ngsi-ld/v1/entities/{parcel_urn}",
            params={"options": "keyValues", "attrs": "location"},
            headers={"Accept": "application/json", "Content-Type": None},
            timeout=10,
        )
        response.raise_for_status()
        location = response.json().get("location")
        if isinstance(location, dict) and location.get("type") == "GeoProperty":
            location = location.get("value")
        if not isinstance(location, dict):
            raise ValueError(f"parcel {parcel_urn} has no geometry")
        return location

    def crop_species() -> str | None:
        from app.api.scenes import _get_crop_species_from_orion
        return asyncio.run(_get_crop_species_from_orion(tenant_id, parcel_urn))

    return Sources(
        rows=lambda index: load_raster_rows(db, tenant_id, parcel_urn, index),
        raster_bytes=raster_bytes,
        parcel_geometry=parcel_geometry,
        crop_species=crop_species,
    )
