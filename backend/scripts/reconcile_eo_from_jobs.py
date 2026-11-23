#!/usr/bin/env python3
"""Rebuild EOProduct entities from completed index jobs already in the database.

Runs INSIDE the vegetation-health backend container (imports the app).

Every completed ``calculate_index`` job stores its zonal statistics and its
sensing date in ``vegetation_jobs.result``. The broker copy of those numbers
lives in an ``EOProduct`` per (parcel, sensing date), with one named Property
per index. The two can drift apart: the statistics are committed to the
database before the broker write, so anything that stops the publish -- a
broker outage, a code path that never published in the first place -- leaves
the numbers on disk and the broker short. Re-running the jobs would re-bill
Copernicus for data that is already computed; this reconciler republishes it
instead.

Idempotent: publishing uses the same create-or-merge upsert as the live path,
so running it twice is indistinguishable from running it once. For tenants in
EO_SATIMG_WRITE_TENANTS the EOSatelliteImagery is written too: a job with a
window (its date columns or the date_range of its result) is a temporal
composite dated by the window end.

Dry-run:  python3 scripts/reconcile_eo_from_jobs.py --tenants acmefarm --dry-run
Apply:    python3 scripts/reconcile_eo_from_jobs.py --tenants acmefarm
Window:   python3 scripts/reconcile_eo_from_jobs.py --tenants acmefarm --since 2026-09-01
"""

import argparse
import collections
import sys
from datetime import date, datetime
from types import SimpleNamespace

from sqlalchemy import text

from app.database import SessionLocal
from app.services.fiware_integration import satimg_enabled, upsert_eo_index

# SAR products are written by their own publisher (backscatter/change-flag
# attributes, a different statistics shape), so they are out of scope here.
SAR_PREFIX = "SAR"


def _iter_jobs(db, tenants, since):
    sql = """
        SELECT j.tenant_id,
               j.entity_id,
               j.parameters->>'index_type'   AS index_type,
               j.result->>'sensing_date'     AS sensing_date,
               j.result->'statistics'        AS statistics,
               j.start_date,
               j.end_date,
               j.result->'date_range'->>'start' AS window_start,
               j.result->'date_range'->>'end'   AS window_end,
               j.result->>'raster_path'      AS raster_path,
               j.result->'interior'          AS interior,
               j.result->'parcel_cloud_fraction' AS parcel_cloud_fraction,
               COALESCE(j.parameters->'bounds', j.result->'geometry') AS geometry,
               s.scene_id                  AS scene_product_id,
               s.sensing_date              AS scene_sensing_date,
               s.acquisition_datetime      AS scene_acquisition_datetime,
               s.cloud_coverage            AS scene_cloud_coverage,
               ST_AsGeoJSON(s.footprint)   AS scene_footprint
        FROM vegetation_jobs j
        LEFT JOIN vegetation_scenes s
          ON s.tenant_id = j.tenant_id AND s.id::text = j.result->>'scene_id'
        WHERE j.deleted_at IS NULL
          AND j.job_type = 'calculate_index'
          AND j.status = 'completed'
          AND j.entity_id IS NOT NULL
          AND j.result ? 'statistics'
          AND j.result->>'sensing_date' IS NOT NULL
          AND j.parameters->>'index_type' NOT LIKE :sar
          AND (:all_tenants OR j.tenant_id = ANY(:tenants))
          AND (:since IS NULL OR (j.result->>'sensing_date')::date >= :since)
        ORDER BY j.result->>'sensing_date', j.parameters->>'index_type'
    """
    rows = db.execute(
        text(sql),
        {
            "sar": f"{SAR_PREFIX}%",
            "all_tenants": not tenants,
            "tenants": tenants or [""],
            "since": since,
        },
    )
    return rows.fetchall()


def publish_satimg(job, stats) -> None:
    """EOSatelliteImagery of one job row; the optical writer logs and never raises."""
    import os

    from app.services import eo_satimg_optical as so
    from app.services.storage import generate_tenant_bucket_name

    if job.index_type.lower() not in so.INDEX_NAMES:
        return
    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(job.tenant_id)
    folder = so.raster_folder(bucket, getattr(job, "raster_path", None), job.index_type)
    # /calculate composites keep the window in the result only. parameters.start_date is
    # NOT a window: single-date jobs carry the search range there.
    start = getattr(job, "start_date", None) or getattr(job, "window_start", None)
    end = getattr(job, "end_date", None) or getattr(job, "window_end", None)
    interior = getattr(job, "interior", None)
    analytics = so.index_analytics(job.index_type, stats)
    analytics["pixelCount"] = stats.get("pixel_count")
    if start and end:
        take = so.composite_take(_as_date(start), _as_date(end), folder)
    elif getattr(job, "scene_product_id", None):
        take = so.scene_take(SimpleNamespace(
            scene_id=job.scene_product_id, sensing_date=_as_date(job.scene_sensing_date),
            acquisition_datetime=job.scene_acquisition_datetime, footprint=job.scene_footprint), folder)
        analytics.update(so.index_analytics(job.index_type, stats, interior=interior))
        analytics["cloudCoverage"] = so.cloud_coverage(job.scene_cloud_coverage)
        analytics["parcelCloudFraction"] = getattr(job, "parcel_cloud_fraction", None)
    else:
        take = so.no_scene_take(date.fromisoformat(job.sensing_date[:10]))
    so.write_optical(job.tenant_id, so.parcel_target(job.entity_id, getattr(job, "geometry", None)), take,
                     analytics, index_type=job.index_type,
                     interior_fraction=(interior or {}).get("valid_fraction") if take.scene else None)


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tenants",
        default="",
        help="comma-separated tenant ids; omit to reconcile every tenant",
    )
    ap.add_argument("--since", help="only jobs whose sensing date is on or after YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args()

    tenants = [t.strip() for t in args.tenants.split(",") if t.strip()]
    since = date.fromisoformat(args.since) if args.since else None

    db = SessionLocal()
    try:
        jobs = _iter_jobs(db, tenants, since)
    finally:
        db.close()

    print(f"{len(jobs)} completed index job(s) in scope")
    if args.dry_run:
        pending = collections.Counter(j.index_type for j in jobs)
        for idx, n in sorted(pending.items()):
            print(f"  would publish {n:4d}  {idx}")
        return 0

    published = collections.Counter()
    failures = []
    for job in jobs:
        stats = dict(job.statistics or {})
        # Copernicus results report valid_pixels; the writer reads pixel_count.
        stats.setdefault("pixel_count", stats.get("valid_pixels", 0))
        entity_id = upsert_eo_index(
            tenant_id=job.tenant_id,
            parcel_id=job.entity_id,
            index_type=job.index_type,
            statistics=stats,
            sensing_date=date.fromisoformat(job.sensing_date),
        )
        if entity_id:
            published[job.index_type] += 1
            if satimg_enabled(job.tenant_id):
                publish_satimg(job, stats)
        else:
            failures.append((job.tenant_id, job.index_type, job.sensing_date))

    for idx, n in sorted(published.items()):
        print(f"  published {n:4d}  {idx}")
    if failures:
        print(f"{len(failures)} failed:", file=sys.stderr)
        for f in failures[:20]:
            print(f"  {f[0]} {f[1]} {f[2]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
