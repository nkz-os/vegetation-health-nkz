"""Recompute the interior measure of already published EOProducts from the stored rasters.

    python -m app.jobs.interior_backfill --tenant T [--parcel URN] [--apply]

Index attributes published as 0 with pixelCount 0 are deleted (never set to null).
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
INDEX_ATTRIBUTES = ("ndvi", "evi", "savi", "ndre", "gndvi", "osavi", "ndmi")
PRODUCT_PAGE = 1000


class NoGeometry(Exception):
    """The parcel does not exist in the broker or carries no location."""


@dataclass
class BackfillReport:
    updated: int = 0
    unchanged: int = 0
    missing_raster: int = 0
    no_geometry: int = 0
    zeros_retired: int = 0
    errors: int = 0
    not_found: int = 0  # zero attribute whose DELETE answered 404: nothing was removed
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


def _short_name(key: str) -> str:
    for prefix in fi._NAME_PREFIXES[1:]:
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


def zero_index_attributes(entity: dict) -> List[str]:
    """Index attribute keys, exactly as the entity carries them (short name or full IRI),
    published with value 0 on a product whose ``pixelCount`` is 0.

    A zero with no valid pixel behind it is not a measurement.
    """
    pixel_count = fi._property_value(fi._attr_by_name(entity, "pixelCount"))
    try:
        if pixel_count is None or int(pixel_count) != 0:
            return []
    except (TypeError, ValueError):
        return []
    zeros = []
    for key, attr in entity.items():
        if _short_name(key) not in INDEX_ATTRIBUTES:
            continue
        value = fi._property_value(attr)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
            zeros.append(key)
    return zeros


def _product_parcel(entity: dict) -> Optional[str]:
    for name in ("hasAgriParcel", "refAgriParcel"):
        rel = fi._attr_by_name(entity, name)
        if isinstance(rel, dict):
            rel = rel.get("object", rel.get("value"))
        if isinstance(rel, str):
            return rel
    return None


def _is_not_found(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code")) in ("NoSuchKey", "404", "NotFound")
    return getattr(response, "status_code", None) == 404


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
        """The parcel's GeoJSON geometry. Raises NoGeometry for a 404 or an entity with no
        location; any other failure propagates."""
        try:
            entity = fi.SyncOrionClient(tenant_id).get_entity(parcel_urn)
        except Exception as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                raise NoGeometry(f"parcel {parcel_urn} not found") from exc
            raise
        location = fi._property_value(fi._attr_by_name(entity or {}, "location"))
        if not isinstance(location, dict) or "coordinates" not in location:
            raise NoGeometry(f"parcel {parcel_urn} has no geometry")
        return location

    def get_product(self, tenant_id: str, entity_id: str) -> Optional[dict]:
        """The whole EOProduct, None on 404 (``?attrs=`` answers 404 on existing entities)."""
        try:
            return fi.SyncOrionClient(tenant_id).get_entity(entity_id)
        except Exception as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                return None
            raise

    def list_products(self, tenant_id: str):
        """Every EOProduct of the tenant, paged."""
        client = fi.SyncOrionClient(tenant_id)
        offset = 0
        while True:
            page = client.query_entities(type="EOProduct", limit=PRODUCT_PAGE, offset=offset)
            yield from page
            if len(page) < PRODUCT_PAGE:
                return
            offset += PRODUCT_PAGE

    def delete_attr(self, tenant_id: str, entity_id: str, attr: str) -> str:
        return fi.delete_eo_attribute(tenant_id, entity_id, attr)

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
                    reader: Optional[Callable[[str], bytes]] = None, broker=None,
                    retire_zeros: bool = True) -> BackfillReport:
    """Recompute the interior measure of the tenant's published EOProducts from stored rasters.

    With ``apply`` False nothing is written and ``updated``/``zeros_retired`` count what would be.
    ``retire_zeros`` also deletes the index attributes published as 0 with no valid pixel.
    """
    report = BackfillReport()
    broker = broker or OrionBroker()
    reader = reader or _storage_reader(tenant_id)
    bucket = os.getenv("VEGETATION_COG_BUCKET") or _tenant_bucket(tenant_id)
    geometries: Dict[str, Optional[dict]] = {}
    touched = set()  # (entity_id, index short name) republished, or to be, in this run

    # The zero list is taken BEFORE any upsert: an upsert rewrites the product's pixelCount
    # (last index wins), which would hide the zeros of the other indices.
    zeros = _zero_snapshot(tenant_id, broker, parcel, report) if retire_zeros else []

    for (parcel_urn, index_type, day), jobs in sorted(_group_jobs(_load_jobs(db, tenant_id, parcel)).items(),
                                                      key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        context = f"tenant={tenant_id} parcel={parcel_urn} index={index_type} date={day}"
        try:
            if parcel_urn not in geometries:
                try:
                    geometries[parcel_urn] = broker.parcel_geometry(tenant_id, parcel_urn)
                except NoGeometry as exc:
                    geometries[parcel_urn] = None
                    report.no_geometry += 1
                    logger.warning("parcel %s skipped (reason=no_geometry): %s", parcel_urn, exc)
                except Exception as exc:
                    geometries[parcel_urn] = None
                    report.errors += 1
                    logger.error("parcel %s skipped (reason=geometry_error) tenant=%s: %s",
                                 parcel_urn, tenant_id, exc, exc_info=True)
            geometry = geometries[parcel_urn]
            if geometry is None:
                continue

            entity_id = fi._entity_id_for_acquisition(tenant_id, parcel_urn, day.isoformat())
            published = _published_index(broker.get_product(tenant_id, entity_id), index_type)
            if published is None:
                report.no_product += 1
                logger.info("%s skipped (reason=no_published_index)", context)
                continue
            published_value, published_fraction = published

            interiors: Dict[str, dict] = {}
            failed = False
            for job in jobs:
                try:
                    measured = interior_from_raster(reader(job["raster_path"]), geometry)
                except Exception as exc:
                    if _is_not_found(exc):
                        logger.warning("raster %s not found: %s", job["raster_path"], exc)
                    else:
                        failed = True
                        logger.error("raster %s unreadable (%s): %s", job["raster_path"], context, exc,
                                     exc_info=True)
                    continue
                if measured is not None:
                    interiors[job["raster_path"]] = measured
            if failed:
                # Choosing among the scenes that did read could pick the wrong one.
                report.errors += 1
                continue
            chosen = pick_job_for_date(jobs, published_value, interiors)
            if chosen is None:
                report.missing_raster += 1
                logger.info("%s skipped (reason=missing_raster)", context)
                continue

            interior = interiors[chosen["raster_path"]]
            same_scene = published_value is not None and _mean6(chosen) == round(published_value, 6)
            if (same_scene and published_fraction is not None
                    and abs(published_fraction - _fraction(interior)) <= FRACTION_TOLERANCE):
                report.unchanged += 1
                continue
            report.updated += 1
            touched.add((entity_id, index_type.lower()))
            logger.info("%s %s (same_scene=%s)", context, "updating" if apply else "would update", same_scene)
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
            logger.error("%s failed: %s", context, exc, exc_info=True)
    if retire_zeros:
        _retire_zeros(tenant_id, broker, apply, report, zeros, touched)
    return report


def _zero_snapshot(tenant_id: str, broker, parcel: Optional[str], report: BackfillReport) -> list:
    """``(entity_id, attribute key)`` of every zero-pixel index, restricted to ``parcel`` if given."""
    zeros = []
    try:
        for entity in broker.list_products(tenant_id):
            if parcel and _product_parcel(entity) != parcel:
                continue
            zeros.extend((entity.get("id"), key) for key in zero_index_attributes(entity))
    except Exception as exc:
        report.errors += 1
        logger.error("zero listing for tenant %s failed: %s", tenant_id, exc, exc_info=True)
    return zeros


def _retire_zeros(tenant_id: str, broker, apply: bool, report: BackfillReport, zeros: list, touched: set) -> None:
    """Delete the listed zero attributes, except the indices this run republished."""
    for entity_id, key in zeros:
        if (entity_id, _short_name(key)) in touched:
            logger.info("%s: %s kept (republished in this run)", entity_id, key)
            continue
        if not apply:
            logger.info("%s: would delete %s (value 0, pixelCount 0)", entity_id, key)
            report.zeros_retired += 1
            continue
        outcome = broker.delete_attr(tenant_id, entity_id, key)
        if outcome == "deleted":
            logger.info("%s: deleted %s (value 0, pixelCount 0)", entity_id, key)
            report.zeros_retired += 1
        elif outcome == "not_found":
            report.not_found += 1
        else:
            report.errors += 1


def _tenant_bucket(tenant_id: str) -> str:
    from app.services.storage import generate_tenant_bucket_name
    return generate_tenant_bucket_name(tenant_id)


def main(argv=None, db=None, out=None) -> int:
    parser = argparse.ArgumentParser(description="Recompute interior statistics of published EOProducts.")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--parcel", help="limit to this parcel URN")
    parser.add_argument("--no-retire-zeros", action="store_true",
                        help="do not delete zero-pixel index attributes")
    parser.add_argument("--apply", action="store_true", help="write to the broker (default: dry run)")
    args = parser.parse_args(argv)
    out = out or sys.stdout
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    own_db = db is None
    if own_db:
        from app import database  # imported late: needs DATABASE_URL, which tests do not set
        db = database.SessionLocal()
    try:
        report = backfill_tenant(db, args.tenant, parcel=args.parcel, apply=args.apply,
                                 retire_zeros=not args.no_retire_zeros)
    finally:
        if own_db:
            db.close()
    label = "APPLIED" if args.apply else "DRY-RUN (nothing written)"
    out.write(f"{label} tenant={args.tenant} updated={report.updated} unchanged={report.unchanged} "
              f"missing_raster={report.missing_raster} no_geometry={report.no_geometry} "
              f"not_found={report.not_found} "
              f"no_product={report.no_product} zeros_retired={report.zeros_retired} errors={report.errors}\n")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
