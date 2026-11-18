"""Recompute the interior measure of already published EOProducts from the stored rasters.

    python -m app.jobs.interior_backfill --tenant T [--parcel URN] [--apply]

Without --apply nothing is written: it counts and logs what it would change.
Nothing is downloaded from Copernicus: the raster each calculation saved is read
back and measured over the parcel interior (``interior_stats``). The parcel cloud
fraction cannot be recomputed (the scene classification is not stored), so it is
left unpublished for these dates. Running it again changes nothing.
"""
import argparse
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, List, Optional

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from sqlalchemy import text

from app.services import fiware_integration as fi
from app.services.interior_stats import interior_mask, interior_statistics

logger = logging.getLogger(__name__)

FRACTION_TOLERANCE = 1e-4


@dataclass
class BackfillReport:
    updated: int = 0
    unchanged: int = 0
    missing_raster: int = 0
    no_geometry: int = 0
    zeros_retired: int = 0
    errors: int = 0
    no_product: int = 0  # job with valid pixels but no published index for the date: not created here


def interior_from_raster(raster, parcel_geojson: dict) -> Optional[dict]:
    """Interior statistics of a stored index raster (GeoTIFF bytes or a path).

    NaN and the file's nodata are both "no value". None when the interior mask has no pixel.
    """
    def _measure(src) -> Optional[dict]:
        arr = src.read(1).astype("float64")
        if src.nodata is not None and np.isfinite(src.nodata):
            arr[arr == src.nodata] = np.nan
        band_meta = {"transform": src.transform, "crs": src.crs, "height": src.height, "width": src.width}
        inside, mask_name = interior_mask(band_meta, parcel_geojson, arr.shape)
        return interior_statistics(arr, inside, mask_name)

    if isinstance(raster, (bytes, bytearray)):
        with MemoryFile(bytes(raster)) as mem, mem.open() as src:
            return _measure(src)
    with rasterio.open(raster) as src:
        return _measure(src)


def _mean6(job: dict) -> Optional[float]:
    mean = (job.get("statistics") or {}).get("mean")
    return round(float(mean), 6) if mean is not None else None


def _fraction(interior: dict) -> float:
    return float(interior["valid_fraction"])


def pick_job_for_date(jobs: List[dict], published_value: Optional[float],
                      interiors: Dict[str, dict]) -> Optional[dict]:
    """The calculation whose raster stands for the published observation of one date.

    Only jobs whose raster was readable (present in ``interiors``, keyed by
    ``raster_path``) qualify. Prefer the job whose ``statistics.mean`` (rounded to 6)
    equals the published value; if none does, the one with the largest valid interior
    fraction. None when no job qualifies.
    """
    usable = [j for j in jobs if interiors.get(j["raster_path"]) is not None]
    if not usable:
        return None
    if published_value is not None:
        matching = [j for j in usable if _mean6(j) == round(float(published_value), 6)]
        if matching:
            return max(matching, key=lambda j: _fraction(interiors[j["raster_path"]]))
    return max(usable, key=lambda j: _fraction(interiors[j["raster_path"]]))


def _load_jobs(db, tenant_id: str, parcel: Optional[str] = None) -> List[dict]:
    """Completed single-scene standard-index calculations of the tenant that kept a raster."""
    from app.models import VegetationJob

    db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
    query = db.query(VegetationJob).filter(
        VegetationJob.tenant_id == tenant_id,
        VegetationJob.job_type == "calculate_index",
        VegetationJob.status == "completed",
        VegetationJob.deleted_at.is_(None),
        VegetationJob.result["raster_path"].astext.isnot(None),
    )
    if parcel:
        query = query.filter(VegetationJob.entity_id == parcel)
    jobs = []
    for job in query.all():
        result = job.result or {}
        index_type = result.get("index_type")
        if (not index_type or index_type == "CUSTOM" or result.get("is_custom")
                or result.get("is_composite") or not result.get("raster_path")
                or not job.entity_id or not result.get("sensing_date")):
            continue
        try:
            sensing_date = date.fromisoformat(str(result["sensing_date"])[:10])
        except ValueError:
            continue
        if not fi.has_valid_pixels(result.get("statistics")):
            continue
        jobs.append({"entity_id": job.entity_id, "index_type": index_type, "sensing_date": sensing_date,
                     "raster_path": result["raster_path"], "statistics": result["statistics"]})
    return jobs


class OrionBroker:
    """The broker operations the job needs; tests inject a fake with the same methods."""

    def parcel_geometry(self, tenant_id: str, parcel_urn: str) -> dict:
        from app.services.zone_backfill import _parcel_geometry
        return _parcel_geometry(tenant_id, parcel_urn)

    def get_product(self, tenant_id: str, entity_id: str) -> Optional[dict]:
        """The whole EOProduct, None on 404 (``?attrs=`` answers 404 on existing entities)."""
        try:
            return fi.SyncOrionClient(tenant_id).get_entity(entity_id)
        except Exception as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                return None
            raise

    def upsert(self, tenant_id: str, parcel_id: str, index_type: str, **kwargs) -> Optional[str]:
        return fi.upsert_eo_index(tenant_id, parcel_id, index_type, **kwargs)


def _storage_reader(tenant_id: str) -> Callable[[str], bytes]:
    """Reader of stored rasters (bytes) from the bucket the calculation wrote to."""
    from app.services.storage import create_storage_service, generate_tenant_bucket_name

    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(tenant_id)
    storage = create_storage_service(storage_type=os.getenv("STORAGE_TYPE", "s3"), default_bucket=bucket)

    def read(raster_path: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
            storage.download_file(raster_path, tmp.name)
            with open(tmp.name, "rb") as fh:
                return fh.read()

    return read


def _published_index(entity: Optional[dict], index_type: str):
    """``(value, interiorValidFraction)`` of the index in an EOProduct; None if the index is absent."""
    attr = fi._attr_by_name(entity or {}, index_type.lower())
    if not isinstance(attr, dict):
        return None
    value = fi._property_value(attr)
    fraction = fi._property_value(fi._attr_by_name(attr, "interiorValidFraction"))
    return (float(value) if value is not None else None,
            float(fraction) if fraction is not None else None)


def _group_jobs(jobs: List[dict]) -> Dict[tuple, List[dict]]:
    groups: Dict[tuple, List[dict]] = {}
    for job in jobs:
        groups.setdefault((job["entity_id"], job["index_type"], job["sensing_date"]), []).append(job)
    return groups


def backfill_tenant(db, tenant_id: str, *, parcel: Optional[str] = None, apply: bool = False,
                    reader: Optional[Callable[[str], bytes]] = None, broker=None) -> BackfillReport:
    """Recompute the interior measure of the tenant's published EOProducts from stored rasters.

    With ``apply`` False nothing is written and ``updated`` counts what would be.
    """
    report = BackfillReport()
    broker = broker or OrionBroker()
    reader = reader or _storage_reader(tenant_id)
    bucket = os.getenv("VEGETATION_COG_BUCKET") or _tenant_bucket(tenant_id)
    geometries: Dict[str, Optional[dict]] = {}

    for (parcel_urn, index_type, day), jobs in sorted(_group_jobs(_load_jobs(db, tenant_id, parcel)).items(),
                                                      key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        try:
            if parcel_urn not in geometries:
                try:
                    geometries[parcel_urn] = broker.parcel_geometry(tenant_id, parcel_urn)
                except Exception as exc:
                    geometries[parcel_urn] = None
                    report.no_geometry += 1
                    logger.warning("parcel %s skipped (reason=no_geometry): %s", parcel_urn, exc)
            geometry = geometries[parcel_urn]
            if geometry is None:
                continue

            entity_id = fi._entity_id_for_acquisition(tenant_id, parcel_urn, day.isoformat())
            published = _published_index(broker.get_product(tenant_id, entity_id), index_type)
            if published is None:
                report.no_product += 1
                logger.info("%s %s %s skipped (reason=no_published_index)", parcel_urn, index_type, day)
                continue
            published_value, published_fraction = published

            interiors: Dict[str, dict] = {}
            for job in jobs:
                try:
                    measured = interior_from_raster(reader(job["raster_path"]), geometry)
                except Exception as exc:
                    logger.warning("raster %s unreadable: %s", job["raster_path"], exc)
                    continue
                if measured is not None:
                    interiors[job["raster_path"]] = measured
            chosen = pick_job_for_date(jobs, published_value, interiors)
            if chosen is None:
                report.missing_raster += 1
                logger.info("%s %s %s skipped (reason=missing_raster)", parcel_urn, index_type, day)
                continue

            interior = interiors[chosen["raster_path"]]
            same_scene = published_value is not None and _mean6(chosen) == round(published_value, 6)
            if (same_scene and published_fraction is not None
                    and abs(published_fraction - _fraction(interior)) <= FRACTION_TOLERANCE):
                report.unchanged += 1
                continue
            report.updated += 1
            logger.info("%s %s %s %s (same_scene=%s)", parcel_urn, index_type, day,
                        "updating" if apply else "would update", same_scene)
            if not apply:
                continue
            raster_url = f"s3://{bucket}/{chosen['raster_path']}"
            eid = broker.upsert(
                tenant_id, parcel_urn, index_type,
                statistics=chosen["statistics"], sensing_date=day, raster_url=raster_url,
                preview_url=raster_url[:-4] + ".png" if raster_url.endswith(".tif") else None,
                interior=interior,
            )
            if not eid:
                report.updated -= 1
                report.errors += 1
        except Exception as exc:
            report.errors += 1
            logger.error("%s %s %s failed: %s", parcel_urn, index_type, day, exc, exc_info=True)
    return report


def _tenant_bucket(tenant_id: str) -> str:
    from app.services.storage import generate_tenant_bucket_name
    return generate_tenant_bucket_name(tenant_id)


def main(argv=None, db=None, out=None) -> int:
    parser = argparse.ArgumentParser(description="Recompute interior statistics of published EOProducts.")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--parcel", help="limit to this parcel URN")
    parser.add_argument("--apply", action="store_true", help="write to the broker (default: dry run)")
    args = parser.parse_args(argv)
    out = out or sys.stdout
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    own_db = db is None
    if own_db:
        from app import database  # imported late: needs DATABASE_URL, which tests do not set
        db = database.SessionLocal()
    try:
        report = backfill_tenant(db, args.tenant, parcel=args.parcel, apply=args.apply)
    finally:
        if own_db:
            db.close()
    label = "APPLIED" if args.apply else "DRY-RUN (nothing written)"
    out.write(f"{label} tenant={args.tenant} updated={report.updated} unchanged={report.unchanged} "
              f"missing_raster={report.missing_raster} no_geometry={report.no_geometry} "
              f"no_product={report.no_product} zeros_retired={report.zeros_retired} errors={report.errors}\n")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
