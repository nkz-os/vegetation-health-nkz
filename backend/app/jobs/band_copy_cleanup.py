"""One-off cleanup of accumulated tenant band copies.

    python -m app.jobs.band_copy_cleanup [--tenant T ...] [--delete]

Without --delete it only lists. With --delete it lists again and deletes what is
eligible in THAT run, so the deletion check always wins over an earlier listing.
A copy is eligible unless its scene still has work in flight (a pending/running
download or calculate_index job, or a fresh pending per-parcel row). The common
cache bucket is never touched: only buckets of tenants are listed.
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from sqlalchemy import text

from app.services import parcel_scenes
from app.services.storage import create_storage_service, generate_tenant_bucket_name
from app.tasks.download_tasks import band_copy_prefix

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_PATH = "vegetation-prime/"  # same default as the download task when a tenant has no config row
IN_PROGRESS = "in_progress"
IN_PROGRESS_COMPOSITE = "in_progress_composite"
PENDING_PARCEL = "pending_parcel"
CALCULATED = "calculated"
GB = 1024 ** 3


@dataclass(frozen=True)
class CopyEntry:
    tenant_id: str
    bucket: str
    prefix: str
    scene_id: str
    size_bytes: int
    eligible: bool
    reason: str


def _tenant_paths(db, tenants: Optional[list]) -> dict:
    """tenant_id -> storage_path for the requested tenants (or all configured ones)."""
    rows = db.execute(text("SELECT tenant_id, storage_path FROM vegetation_config")).fetchall()
    configured = {r[0]: (r[1] or "") for r in rows}
    if tenants:
        return {t: configured.get(t, DEFAULT_STORAGE_PATH) for t in tenants}
    return configured


def _tenant_context(db, tenant_id: str) -> None:
    db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})


def _in_progress_scenes(db, tenant_id: str) -> set:
    """Scene product ids named by a pending/running download or calculate_index job of the tenant.

    A job may name the scene by product id or by the VegetationScene UUID; both resolve to the product id.
    """
    rows = db.execute(
        text(
            "SELECT DISTINCT COALESCE(s.scene_id, j.parameters->>'scene_id') "
            "FROM vegetation_jobs j "
            "LEFT JOIN vegetation_scenes s ON s.tenant_id = j.tenant_id AND s.id::text = j.parameters->>'scene_id' "
            "WHERE j.tenant_id = :t AND j.job_type IN ('download', 'calculate_index') "
            "AND j.status IN ('pending', 'running') AND j.parameters->>'scene_id' IS NOT NULL"
        ),
        {"t": tenant_id},
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def _composite_scenes(db, tenant_id: str) -> set:
    """Scenes of the tenant inside the date range of a pending/running calculate_index job.

    A temporal composite reads every tenant scene in [start, end] (dates from the job columns or its
    parameters). Any such job with a range is counted, whether or not it also names a scene.
    """
    rows = db.execute(
        text(
            "SELECT DISTINCT s.scene_id FROM vegetation_jobs j "
            "JOIN vegetation_scenes s ON s.tenant_id = j.tenant_id "
            "AND s.sensing_date >= COALESCE(j.start_date, (j.parameters->>'start_date')::date) "
            "AND s.sensing_date <= COALESCE(j.end_date, (j.parameters->>'end_date')::date) "
            "WHERE j.tenant_id = :t AND j.job_type = 'calculate_index' AND j.status IN ('pending', 'running') "
            "AND COALESCE(j.start_date, (j.parameters->>'start_date')::date) IS NOT NULL "
            "AND COALESCE(j.end_date, (j.parameters->>'end_date')::date) IS NOT NULL"
        ),
        {"t": tenant_id},
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def busy_scenes(db, tenant_id: str, now: datetime) -> dict:
    """scene product id -> reason, for every scene of the tenant with work in flight."""
    _tenant_context(db, tenant_id)
    busy = {}
    for scenes, reason in (
        (_fresh_pending_scenes(db, tenant_id, now), PENDING_PARCEL),
        (_composite_scenes(db, tenant_id), IN_PROGRESS_COMPOSITE),
        (_in_progress_scenes(db, tenant_id), IN_PROGRESS),
    ):
        for scene_id in scenes:
            busy[scene_id] = reason
    return busy


def _fresh_pending_scenes(db, tenant_id: str, now: datetime) -> set:
    """Scenes with a per-parcel row still pending and younger than the stale window."""
    rows = db.execute(
        text(
            "SELECT DISTINCT scene_id FROM vegetation_parcel_scenes "
            "WHERE tenant_id = :t AND status = 'pending' AND updated_at >= :cutoff"
        ),
        {"t": tenant_id, "cutoff": now - parcel_scenes.STALE_PENDING},
    ).fetchall()
    return {r[0] for r in rows}


def list_copies(db, s3, tenants: Optional[list] = None, now: Optional[datetime] = None) -> list:
    """List the band copies of each tenant, one entry per scene, with eligibility. Never deletes."""
    now = now or datetime.now(timezone.utc)
    entries = []
    for tenant_id, storage_path in sorted(_tenant_paths(db, tenants).items()):
        bucket = generate_tenant_bucket_name(tenant_id)
        root = band_copy_prefix(storage_path, "")
        sizes = {}
        for obj in s3.list_objects(root, bucket):
            rest = obj["Key"][len(root):]
            scene_id = rest.split("/", 1)[0]
            if not scene_id:
                continue
            sizes[scene_id] = sizes.get(scene_id, 0) + int(obj.get("Size", 0) or 0)
        if not sizes:
            continue
        busy = busy_scenes(db, tenant_id, now)
        for scene_id in sorted(sizes):
            if scene_id in busy:
                eligible, reason = False, busy[scene_id]
            else:
                eligible, reason = True, CALCULATED
            entries.append(CopyEntry(
                tenant_id=tenant_id, bucket=bucket, prefix=f"{root}{scene_id}/", scene_id=scene_id,
                size_bytes=sizes[scene_id], eligible=eligible, reason=reason,
            ))
    return entries


def delete_copies(s3, entries: Iterable[CopyEntry], recheck: Optional[Callable] = None,
                  skipped: Optional[list] = None) -> tuple:
    """Delete the eligible copies from their own tenant bucket. Returns (scenes, bytes).

    `recheck(entry)` is called right before each delete and returns a busy reason or None; a busy scene is
    skipped (appended to `skipped` as (entry, reason)), so work that started after listing wins.
    """
    scenes = 0
    freed = 0
    for entry in entries:
        if not entry.eligible:
            continue
        reason = recheck(entry) if recheck else None
        if reason:
            logger.warning(
                "band copy skipped tenant=%s scene=%s reason=%s", entry.tenant_id, entry.scene_id, reason,
            )
            if skipped is not None:
                skipped.append((entry, reason))
            continue
        s3.delete_prefix(entry.prefix, entry.bucket)
        scenes += 1
        freed += entry.size_bytes
        logger.info(
            "band copy deleted tenant=%s bucket=%s prefix=%s bytes=%d",
            entry.tenant_id, entry.bucket, entry.prefix, entry.size_bytes,
        )
    return scenes, freed


def _print_listing(entries: list, out) -> None:
    out.write("tenant\tscene\tsize_gb\teligible\treason\n")
    per_tenant = {}
    for e in entries:
        out.write(f"{e.tenant_id}\t{e.scene_id}\t{e.size_bytes / GB:.3f}\t{'yes' if e.eligible else 'no'}\t{e.reason}\n")
        scenes, total, eligible_total = per_tenant.get(e.tenant_id, (0, 0, 0))
        per_tenant[e.tenant_id] = (
            scenes + 1, total + e.size_bytes, eligible_total + (e.size_bytes if e.eligible else 0),
        )
    for tenant_id, (scenes, total, eligible_total) in sorted(per_tenant.items()):
        out.write(
            f"TOTAL\t{tenant_id}\t{scenes} scenes\t{total / GB:.3f} GB\t{eligible_total / GB:.3f} GB eligible\n"
        )
    out.write(
        f"TOTAL\tALL\t{len(entries)} scenes\t{sum(e.size_bytes for e in entries) / GB:.3f} GB\t"
        f"{sum(e.size_bytes for e in entries if e.eligible) / GB:.3f} GB eligible\n"
    )


def main(argv=None, db=None, s3=None, out=None) -> int:
    parser = argparse.ArgumentParser(description="List (default) or delete accumulated tenant band copies.")
    parser.add_argument("--tenant", action="append", help="limit to this tenant (repeatable)")
    parser.add_argument("--delete", action="store_true", help="delete the eligible copies (default: list only)")
    args = parser.parse_args(argv)
    out = out or sys.stdout
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    own_db = db is None
    if own_db:
        from app import database  # imported late: needs DATABASE_URL, which tests do not set
        db = database.SessionLocal()
    try:
        if s3 is None:
            import os
            s3 = create_storage_service(storage_type=os.getenv("STORAGE_TYPE", "s3"))
        if not hasattr(s3, "list_objects") or not hasattr(s3, "delete_prefix"):
            logger.error("storage backend cannot list objects; use STORAGE_TYPE=s3")
            return 2
        entries = list_copies(db, s3, args.tenant)
        _print_listing(entries, out)
        logger.info("band copies listed: scenes=%d eligible=%d delete=%s",
                    len(entries), sum(1 for e in entries if e.eligible), args.delete)
        if args.delete:
            skipped = []
            scenes, freed = delete_copies(
                s3, entries,
                recheck=lambda e: busy_scenes(db, e.tenant_id, datetime.now(timezone.utc)).get(e.scene_id),
                skipped=skipped,
            )
            for entry, reason in skipped:
                out.write(f"SKIPPED\t{entry.tenant_id}\t{entry.scene_id}\t{reason}\n")
            out.write(f"DELETED\t{scenes} scenes\t{freed / GB:.3f} GB\n")
            logger.info("band copies deleted: scenes=%d bytes=%d", scenes, freed)
        return 0
    finally:
        if own_db:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
