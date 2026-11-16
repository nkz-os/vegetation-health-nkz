"""Multi-date zoning: which index dates are used."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.jobs.zoning_multidate.dates import (
    RasterRow,
    load_raster_rows,
    raster_rows_from_jobs,
    select_dates,
)


def _row(day, created, path=None, pending=False, job="j"):
    return RasterRow(job_id=job, sensing_date=day, raster_path=path, pending=pending,
                     created_at=created)


def test_select_dates_dedups_by_date_latest_created():
    rows = [
        _row(date(2026, 5, 9), datetime(2026, 5, 10), "a.tif", job="old"),
        _row(date(2026, 5, 9), datetime(2026, 6, 1), "b.tif", job="new"),
    ]
    out = select_dates(rows, today=date(2026, 11, 16), years_back=2)
    assert [r.job_id for r in out] == ["new"]


def test_select_dates_prefers_materialised_row():
    rows = [
        _row(date(2026, 5, 9), datetime(2026, 6, 1), None, pending=True, job="pending"),
        _row(date(2026, 5, 9), datetime(2026, 5, 10), "a.tif", job="ready"),
    ]
    out = select_dates(rows, today=date(2026, 11, 16), years_back=2)
    assert [r.job_id for r in out] == ["ready"]


def test_select_dates_window_years_back():
    rows = [
        _row(date(2024, 11, 15), datetime(2024, 11, 16), "old.tif", job="old"),
        _row(date(2024, 11, 17), datetime(2024, 11, 18), "in.tif", job="in"),
        _row(date(2026, 11, 17), datetime(2026, 11, 17), "future.tif", job="future"),
    ]
    out = select_dates(rows, today=date(2026, 11, 16), years_back=2)
    assert [r.job_id for r in out] == ["in"]


def test_select_dates_sorted_by_date():
    rows = [
        _row(date(2026, 5, 9), datetime(2026, 5, 10), "b.tif", job="b"),
        _row(date(2025, 5, 9), datetime(2025, 5, 10), "a.tif", job="a"),
    ]
    assert [r.job_id for r in select_dates(rows, today=date(2026, 11, 16), years_back=2)] == ["a", "b"]


def _job(result, job_id="j1", created=datetime(2026, 5, 10)):
    return SimpleNamespace(id=job_id, result=result, created_at=created)


def test_rows_from_jobs_skip_composites_and_unusable():
    jobs = [
        _job({"sensing_date": "2026-05-09", "raster_path": "a.tif"}, "ok"),
        _job({"sensing_date": "2026-05-10", "raster_path": None, "raster_pending": True}, "pend"),
        _job({"sensing_date": "2026-05-11", "raster_path": "c.tif", "is_composite": True}, "comp"),
        _job({"sensing_date": "2026-05-12", "raster_path": None}, "none"),
        _job({"raster_path": "d.tif"}, "nodate"),
    ]
    rows = raster_rows_from_jobs(jobs)
    assert [(r.job_id, r.pending) for r in rows] == [("ok", False), ("pend", True)]
    assert rows[0].sensing_date == date(2026, 5, 9)


def test_load_raster_rows_queries_completed_index_jobs():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        _job({"sensing_date": "2026-05-09", "raster_path": "a.tif"}, "ok"),
    ]
    rows = load_raster_rows(db, "acme", "urn:ngsi-ld:AgriParcel:p1", "NDVI")
    assert [r.job_id for r in rows] == ["ok"]
    from sqlalchemy.dialects import postgresql
    sql = " ".join(
        str(c.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for c in db.query.return_value.filter.call_args.args
    )
    assert "deleted_at IS NULL" in sql
    assert "'index_type'" in sql and "'NDVI'" in sql
    assert "'calculate_index'" in sql and "'completed'" in sql
