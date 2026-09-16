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
so running it twice is indistinguishable from running it once.

Dry-run:  python3 scripts/reconcile_eo_from_jobs.py --tenants montiko --dry-run
Apply:    python3 scripts/reconcile_eo_from_jobs.py --tenants montiko
Window:   python3 scripts/reconcile_eo_from_jobs.py --tenants montiko --since 2026-09-01
"""

import argparse
import collections
import sys
from datetime import date

from sqlalchemy import text

from app.database import SessionLocal
from app.services.fiware_integration import upsert_eo_index

# SAR products are written by their own publisher (backscatter/change-flag
# attributes, a different statistics shape), so they are out of scope here.
SAR_PREFIX = "SAR"


def _iter_jobs(db, tenants, since):
    sql = """
        SELECT tenant_id,
               entity_id,
               parameters->>'index_type'   AS index_type,
               result->>'sensing_date'     AS sensing_date,
               result->'statistics'        AS statistics
        FROM vegetation_jobs
        WHERE deleted_at IS NULL
          AND job_type = 'calculate_index'
          AND status = 'completed'
          AND entity_id IS NOT NULL
          AND result ? 'statistics'
          AND result->>'sensing_date' IS NOT NULL
          AND parameters->>'index_type' NOT LIKE :sar
          AND (:all_tenants OR tenant_id = ANY(:tenants))
          AND (:since IS NULL OR (result->>'sensing_date')::date >= :since)
        ORDER BY result->>'sensing_date', parameters->>'index_type'
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
