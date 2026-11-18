"""Brakes for scene acquisition: per-tenant scene budget per pass and free-disk check.

All limits are read from the environment at use time (tests patch it).
"""
import logging
import os
import shutil
from datetime import datetime, timedelta

from sqlalchemy import func

from app.models.jobs import VegetationJob

logger = logging.getLogger(__name__)

BUDGET_WINDOW = timedelta(hours=20)


def check_path() -> str:
    return os.getenv("VEG_DISK_CHECK_PATH", "/tmp")


def free_gb(path: str | None = None) -> float:
    """Free space, in GB, of the filesystem holding `path` (default VEG_DISK_CHECK_PATH)."""
    return shutil.disk_usage(path or check_path()).free / 1e9


def disk_ok() -> tuple[bool, float, float]:
    """(ok, free_gb, threshold_gb). A threshold <= 0 disables the check."""
    threshold = float(os.getenv("VEG_MIN_FREE_GB", "20"))
    free = free_gb()
    if threshold <= 0:
        return True, free, threshold
    return free >= threshold, free, threshold


class LowDiskSpace(Exception):
    def __init__(self, free: float, threshold: float, path: str):
        self.free = free
        self.threshold = threshold
        self.path = path
        super().__init__(f"Low disk space: {free:.1f} GB free < {threshold:.1f} GB at {path}")


def tenant_budget(db, tenant_id: str, now: datetime) -> int:
    """New scenes this tenant may still enqueue in this pass.

    VEG_MAX_NEW_SCENES_PER_PASS minus the subscription downloads created in the last 20 h.
    """
    cap = int(os.getenv("VEG_MAX_NEW_SCENES_PER_PASS", "20"))
    recent = db.query(func.count(VegetationJob.id)).filter(
        VegetationJob.tenant_id == tenant_id,
        VegetationJob.job_type == "download",
        VegetationJob.parameters.has_key("subscription_id"),  # noqa: W601 (JSONB operator)
        VegetationJob.created_at >= now - BUDGET_WINDOW,
    ).scalar() or 0
    return max(0, cap - int(recent))


def _sort_key(scene: dict) -> str:
    return str(scene.get("datetime") or scene.get("sensing_date") or "")


def select_batch(scenes: list[dict], seen: set[str], budget: int) -> tuple[list[dict], bool]:
    """Oldest unseen scenes first, at most `budget`; `capped` is True when more were left."""
    pending = [s for s in sorted(scenes, key=_sort_key) if s["id"] not in seen]
    batch = pending[:max(0, budget)]
    return batch, len(pending) > len(batch)
