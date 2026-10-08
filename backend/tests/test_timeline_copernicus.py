"""Timeline + per-date results for Copernicus acquisitions.

Copernicus calculate_index jobs carry no scene_id and usually a pending
raster; the timeline must still list them and /results must scope by date.
These drive the real handlers with a mocked session.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.api.entities import build_timeline, get_available_scenes
from app.api import scenes as scenes_mod

USER = {"tenant_id": "t1", "user_id": "u1"}
ENTITY = "urn:ngsi-ld:AgriParcel:p1"
BASE = datetime(2026, 9, 1)


def _job(sensing_date, mean, *, scene_id=None, raster_path=None, pending=True,
         index_type="NDVI", age=0):
    j = MagicMock()
    j.id = uuid4()
    j.tenant_id = "t1"
    j.entity_id = ENTITY
    j.created_at = BASE - timedelta(minutes=age)
    j.result = {
        "index_type": index_type,
        "index_key": index_type,
        "sensing_date": sensing_date,
        "raster_path": raster_path,
        "raster_pending": pending,
        "statistics": {"mean": mean},
    }
    if scene_id:
        j.result["scene_id"] = scene_id
    return j


def test_build_timeline_keeps_copernicus_jobs_without_scene():
    jobs = [_job("2026-08-08", 0.41), _job("2026-08-03", 0.31)]
    out = build_timeline(jobs, {})
    assert [p["date"] for p in out] == ["2026-08-03", "2026-08-08"]
    assert all(p["scene_id"] is None for p in out)
    assert out[0]["id"] == "2026-08-03"
    assert out[0]["mean_value"] == pytest.approx(0.31)
    assert out[0]["raster_pending"] is True


def test_build_timeline_newest_job_wins_per_date():
    # newest first, as the endpoint orders them
    jobs = [
        _job("2026-08-03", 0.50, raster_path="a/NDVI.tif", pending=False, age=0),
        _job("2026-08-03", 0.30, age=10),
    ]
    out = build_timeline(jobs, {})
    assert len(out) == 1
    assert out[0]["mean_value"] == pytest.approx(0.50)
    assert out[0]["raster_path"] == "a/NDVI.tif"
    assert out[0]["raster_pending"] is False


def test_build_timeline_scene_jobs_use_scene_identity_and_cloud():
    sid = str(uuid4())
    out = build_timeline(
        [_job("2026-09-15", 0.2, scene_id=sid, raster_path="s/VV.tif", pending=False)],
        {sid: "12.5"},
    )
    assert out[0]["scene_id"] == sid
    assert out[0]["id"] == sid
    assert out[0]["local_cloud_pct"] == "12.5"


def test_build_timeline_skips_jobs_without_date():
    assert build_timeline([_job(None, 0.3)], {}) == []


def _capturing_db(jobs):
    """Session mock that records every .filter() clause."""
    captured = []
    db = MagicMock()
    chain = MagicMock()

    def _filter(*clauses):
        captured.extend(clauses)
        return chain

    db.query.return_value.filter.side_effect = _filter
    chain.order_by.return_value.limit.return_value.all.return_value = jobs
    chain.count.return_value = 0
    chain.all.return_value = []
    return db, captured


def _sql(clauses):
    return " ".join(
        str(c.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for c in clauses
    )


@pytest.mark.asyncio
async def test_available_scenes_includes_pending_rasters():
    db, captured = _capturing_db([_job("2026-08-03", 0.31)])
    out = await get_available_scenes(ENTITY, index_type="NDVI", current_user=USER, db=db)
    assert out["count"] == 1
    assert out["timeline"][0]["date"] == "2026-08-03"
    assert "raster_pending" in _sql(captured)


@pytest.mark.asyncio
async def test_results_scoped_by_sensing_date():
    job = _job("2026-08-03", 0.31)

    async def _materialize(db_, j):
        j.result["raster_path"] = "t1/copernicus/2026-08-03/NDVI.tif"
        j.result["raster_pending"] = False

    db, captured = _capturing_db([job])
    with patch("app.services.copernicus_raster.materialize_if_pending", side_effect=_materialize):
        out = await scenes_mod.get_entity_results(
            ENTITY, scene_id=None, sensing_date=date(2026, 8, 3), current_user=USER, db=db,
        )

    assert "2026-08-03" in _sql(captured)
    assert out["sensing_date"] == "2026-08-03"
    assert out["indices"]["NDVI"]["raster_path"].endswith("NDVI.tif")
    assert out["indices"]["NDVI"]["sensing_date"] == "2026-08-03"


@pytest.mark.asyncio
async def test_results_without_date_has_no_date_clause():
    db, captured = _capturing_db([])
    out = await scenes_mod.get_entity_results(
        ENTITY, scene_id=None, sensing_date=None, current_user=USER, db=db,
    )
    assert "sensing_date" not in _sql(captured)
    assert out["sensing_date"] is None
