"""Backfill planner: real cursor, per-tenant budget, disk brake and failed searches."""
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
os.environ.setdefault("CDSE_S3_ACCESS_KEY", "k")
os.environ.setdefault("CDSE_S3_SECRET_KEY", "s")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import pytest

import app.tasks.download_tasks as dt
import app.tasks.scheduler as sched
from app.models import VegetationJob
from app.services import parcel_scenes

TENANT = "acme"
PARCEL = "urn:ngsi-ld:AgriParcel:parcel-a"
SCENE = "S2A_TEST_SCENE"
GEOM = {"type": "Polygon", "coordinates": [[[-1.6, 42.8], [-1.5, 42.8], [-1.5, 42.9], [-1.6, 42.9], [-1.6, 42.8]]]}
TODAY = date(2026, 11, 10)


def _sub(last_run_at=None):
    sub = MagicMock()
    sub.id = uuid.uuid4()
    sub.tenant_id = TENANT
    sub.entity_id = PARCEL
    sub.status = "active"
    sub.index_types = ["NDVI"]
    sub.geometry = object()
    sub.start_date = date(2026, 4, 1)
    sub.frequency = "daily"
    sub.last_run_at = last_run_at
    sub.next_run_at = None
    sub.last_error = None
    return sub


def _catalog(n, first=date(2026, 5, 1), step=4):
    return [
        {"id": f"scene-{i:03d}", "sensing_date": (first + timedelta(days=step * i)).isoformat(),
         "datetime": f"{(first + timedelta(days=step * i)).isoformat()}T10:50:00Z", "cloud_cover": 5}
        for i in range(n)
    ]


def _tomorrow_2am():
    return datetime.combine(datetime.now(timezone.utc).date() + timedelta(days=1), datetime.min.time().replace(hour=2))


@contextmanager
def _planner(sub, client, *, seen=lambda: set(), budget=lambda: 20, disk=(True, 100.0, 20.0)):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = sub
    shape = MagicMock()
    shape.bounds = (-1.6, 42.8, -1.5, 42.9)
    shape.geom_type = "Polygon"
    shape.__geo_interface__ = GEOM
    record = MagicMock()
    with patch.object(sched, "get_db_session", return_value=iter([db])), \
         patch("geoalchemy2.shape.to_shape", return_value=shape), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient", return_value=client), \
         patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
               return_value={"client_id": "i", "client_secret": "s"}), \
         patch.object(sched.acquisition_guard, "disk_ok", return_value=disk), \
         patch.object(sched.acquisition_guard, "tenant_budget", side_effect=lambda *a, **k: budget()), \
         patch.object(sched.parcel_scenes, "scenes_seen", side_effect=lambda *a, **k: seen()), \
         patch.object(sched.parcel_scenes, "record", record), \
         patch.object(sched, "download_sentinel2_scene") as download:
        yield db, record, download


def _client(scenes=None, error=None):
    client = MagicMock()
    if error is not None:
        client.search_scenes.side_effect = error
    else:
        client.search_scenes.return_value = scenes
    client.search_s1_scenes.return_value = []
    return client


def test_backfill_converges_over_passes():
    catalog = _catalog(45)
    state = set()          # parcel_scenes in memory
    sub = _sub()
    enqueued = []
    per_pass = {"n": 0}
    client = MagicMock()
    client.search_s1_scenes.return_value = []
    windows = []

    def search(intersects, start_date, end_date, **kw):
        windows.append((start_date, end_date))
        return [s for s in catalog if start_date <= date.fromisoformat(s["sensing_date"]) <= end_date]

    client.search_scenes.side_effect = search

    def rec(tenant, entity, scene_id, status, **kw):
        state.add(scene_id)
        enqueued.append(scene_id)

    for n in range(3):
        per_pass["n"] = 0
        start = (sub.last_run_at.date() - timedelta(days=5)) if sub.last_run_at else sub.start_date
        with _planner(sub, client, seen=lambda: set(state), budget=lambda: 20 - per_pass["n"]) as (db, record, download):
            record.side_effect = rec
            download.delay.side_effect = lambda **kw: per_pass.__setitem__("n", per_pass["n"] + 1)
            sched.check_and_process_entity.run(str(sub.id), start.isoformat(), TODAY.isoformat())
        if n < 2:
            assert sub.last_run_at is not None and sub.last_run_at.date() < TODAY
            assert sub.next_run_at == _tomorrow_2am()
    assert sorted(enqueued) == sorted(s["id"] for s in catalog)
    assert len(enqueued) == len(set(enqueued)) == 45
    assert sub.last_run_at == datetime(2026, 11, 10, tzinfo=timezone.utc)
    assert sub.last_error is None


def test_cursor_is_last_enqueued_scene_date_when_capped():
    sub = _sub()
    catalog = _catalog(30)
    with _planner(sub, _client(catalog), budget=lambda: 20) as (db, record, download):
        sched.check_and_process_entity.run(str(sub.id), "2026-04-01", TODAY.isoformat())
    assert download.delay.call_count == 20
    last = date.fromisoformat(catalog[19]["sensing_date"])
    assert sub.last_run_at == datetime(last.year, last.month, last.day, tzinfo=timezone.utc)


def test_budget_zero_with_pending_scenes_keeps_cursor():
    previous = datetime(2026, 9, 1)
    sub = _sub(last_run_at=previous)
    with _planner(sub, _client(_catalog(5)), budget=lambda: 0) as (db, record, download):
        sched.check_and_process_entity.run(str(sub.id), "2026-08-27", TODAY.isoformat())
    download.delay.assert_not_called()
    assert sub.last_run_at == previous
    assert sub.next_run_at == _tomorrow_2am()


def test_search_failure_keeps_cursor():
    previous = datetime(2026, 9, 1)
    sub = _sub(last_run_at=previous)
    with _planner(sub, _client(error=Exception("Scene search failed: 429"))) as (db, record, download):
        sched.check_and_process_entity.run(str(sub.id), "2026-08-27", TODAY.isoformat())
    assert sub.last_run_at == previous
    assert "Scene search failed: 429" in sub.last_error
    assert sub.next_run_at == _tomorrow_2am()
    download.delay.assert_not_called()


def test_low_disk_skips_and_reports(caplog):
    previous = datetime(2026, 9, 1)
    sub = _sub(last_run_at=previous)
    client = _client(_catalog(3))
    with caplog.at_level("ERROR"), \
         _planner(sub, client, disk=(False, 30.0, 40.0)) as (db, record, download):
        sched.check_and_process_entity.run(str(sub.id), "2026-08-27", TODAY.isoformat())
    client.search_scenes.assert_not_called()
    download.delay.assert_not_called()
    assert sub.last_error.startswith("Low disk space")
    assert sub.last_run_at == previous
    assert any("Low disk space" in r.getMessage() for r in caplog.records)


def test_clean_pass_clears_last_error_and_moves_cursor_to_window_end():
    sub = _sub()
    sub.last_error = "Search failed: old"
    with _planner(sub, _client(_catalog(3))) as (db, record, download):
        sched.check_and_process_entity.run(str(sub.id), "2026-04-01", TODAY.isoformat())
    assert download.delay.call_count == 3
    assert sub.last_error is None
    assert sub.last_run_at == datetime(2026, 11, 10, tzinfo=timezone.utc)


def test_process_subscriptions_no_longer_moves_cursor_and_uses_lookback(monkeypatch):
    monkeypatch.delenv("VEG_SEARCH_LOOKBACK_DAYS", raising=False)
    previous = datetime(2026, 11, 1, 2, 0)
    sub = _sub(last_run_at=previous)
    new_sub = _sub()
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [sub, new_sub]
    with patch.object(sched, "get_db_session", return_value=iter([db])), \
         patch.object(sched, "check_and_process_entity") as task:
        sched.process_subscriptions.run()
    assert sub.last_run_at == previous
    assert new_sub.last_run_at is None
    kwargs = [c.kwargs for c in task.delay.call_args_list]
    assert kwargs[0]["start_date"] == "2026-10-27"
    assert kwargs[1]["start_date"] == "2026-04-01"
    assert sub.next_run_at is not None


def test_process_subscriptions_lookback_env_and_start_date_floor(monkeypatch):
    monkeypatch.setenv("VEG_SEARCH_LOOKBACK_DAYS", "10")
    sub = _sub(last_run_at=datetime(2026, 4, 3))
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [sub]
    with patch.object(sched, "get_db_session", return_value=iter([db])), \
         patch.object(sched, "check_and_process_entity") as task:
        sched.process_subscriptions.run()
    assert task.delay.call_args.kwargs["start_date"] == "2026-04-01"  # never before start_date


# ---- download brake --------------------------------------------------------

def test_download_raises_low_disk_before_copernicus():
    job = MagicMock()
    job.id = uuid.uuid4()
    params = {"scene_id": SCENE, "bbox": [-1.6, 42.8, -1.5, 42.9], "bounds": GEOM,
              "entity_id": PARCEL, "local_cloud_threshold": 100}
    client = MagicMock()
    client.get_scene_item.return_value = {"id": SCENE, "sensing_date": "2026-11-01", "cloud_cover": 5, "geometry": GEOM}
    db = MagicMock()

    def query(*models):
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = job if models[0] is VegetationJob else None
        return q

    db.query.side_effect = query
    with patch.object(dt, "get_db_session", return_value=iter([db])), \
         patch.object(dt, "CopernicusDataSpaceClient", return_value=client), \
         patch.object(dt, "get_copernicus_credentials_with_fallback", return_value=None), \
         patch.object(dt, "create_storage_service", return_value=MagicMock()), \
         patch("app.services.scl_validation.compute_local_cloud_pct", return_value=0.0), \
         patch("app.services.usage_tracker.UsageTracker"), \
         patch.object(dt.acquisition_guard, "disk_ok", return_value=(False, 30.0, 40.0)), \
         patch.object(dt.parcel_scenes, "record") as record, \
         patch.object(dt.download_sentinel2_scene, "update_state"):
        with pytest.raises(dt.acquisition_guard.LowDiskSpace, match="Low disk space"):
            dt.download_sentinel2_scene.run(str(job.id), TENANT, params)
    client.download_scene_bands.assert_not_called()
    job.mark_failed.assert_called_once()
    record.assert_called_once_with(TENANT, PARCEL, SCENE, parcel_scenes.FAILED, job_id=job.id)
