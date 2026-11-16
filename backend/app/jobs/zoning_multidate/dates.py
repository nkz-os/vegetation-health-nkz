"""Which index dates a multi-date zoning uses."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import or_


@dataclass(frozen=True)
class RasterRow:
    job_id: str
    sensing_date: date
    raster_path: str | None
    pending: bool
    created_at: datetime


def _truthy(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def raster_rows_from_jobs(jobs: Iterable[Any]) -> list[RasterRow]:
    """Single-date rasters (ready or pending) from completed index jobs; composites skipped."""
    rows: list[RasterRow] = []
    for job in jobs:
        result = job.result or {}
        if _truthy(result.get("is_composite")):
            continue
        raw_date = result.get("sensing_date")
        if not raw_date:
            continue
        path = result.get("raster_path")
        pending = _truthy(result.get("raster_pending"))
        if not path and not pending:
            continue
        rows.append(RasterRow(
            job_id=str(job.id),
            sensing_date=date.fromisoformat(str(raw_date)[:10]),
            raster_path=path or None,
            pending=bool(pending and not path),
            created_at=job.created_at,
        ))
    return rows


def load_raster_rows(db, tenant_id: str, parcel_id: str, index: str) -> list[RasterRow]:
    """Completed, non-deleted index jobs of one parcel and index (same filter as available-raster-dates)."""
    from app.models import VegetationJob

    jobs = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == parcel_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
            VegetationJob.result["index_type"].astext == index,
            or_(
                VegetationJob.result["raster_path"].astext.isnot(None),
                VegetationJob.result["raster_pending"].astext == "true",
            ),
        )
        .all()
    )
    return raster_rows_from_jobs(jobs)


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def select_dates(rows: list[RasterRow], *, today: date, years_back: int) -> list[RasterRow]:
    """One row per sensing date inside [today - years_back, today], oldest first.

    Per date: a materialised raster beats a pending one, then the latest job wins.
    """
    start = _years_before(today, years_back)
    best: dict[date, RasterRow] = {}
    for row in rows:
        if not (start <= row.sensing_date <= today):
            continue
        current = best.get(row.sensing_date)
        key = (row.raster_path is not None, row.created_at)
        if current is None or key > (current.raster_path is not None, current.created_at):
            best[row.sensing_date] = row
    return [best[d] for d in sorted(best)]
