"""Acquisition guard: oldest-first batches, per-tenant budget and free-disk threshold."""
from collections import namedtuple
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.services import acquisition_guard as guard

Usage = namedtuple("Usage", "total used free")


def _scenes(n, start_day=1):
    # newest first, as the STAC returns them
    return [
        {"id": f"s{i:02d}", "sensing_date": f"2026-06-{i:02d}", "datetime": f"2026-06-{i:02d}T10:50:00Z"}
        for i in range(start_day + n - 1, start_day - 1, -1)
    ]


def test_select_batch_oldest_first_and_capped():
    scenes = _scenes(28) + [
        {"id": f"t{i:02d}", "sensing_date": f"2026-07-{i:02d}", "datetime": f"2026-07-{i:02d}T10:50:00Z"}
        for i in range(11, 0, -1)
    ]
    assert len(scenes) == 39
    seen = {"s01", "s02"}
    batch, capped = guard.select_batch(scenes, seen, 20)
    assert capped is True
    assert [s["id"] for s in batch] == [f"s{i:02d}" for i in range(3, 23)]


def test_select_batch_not_capped():
    batch, capped = guard.select_batch(_scenes(7), {"s01", "s02"}, 20)
    assert len(batch) == 5 and capped is False
    assert [s["id"] for s in batch] == ["s03", "s04", "s05", "s06", "s07"]


def test_select_batch_zero_budget_is_capped_when_pending():
    batch, capped = guard.select_batch(_scenes(3), set(), 0)
    assert batch == [] and capped is True
    assert guard.select_batch(_scenes(3), {"s01", "s02", "s03"}, 0) == ([], False)


def test_disk_ok_threshold(monkeypatch):
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda path: Usage(100e9, 70e9, 30e9))
    monkeypatch.setenv("VEG_MIN_FREE_GB", "40")
    assert guard.disk_ok() == (False, 30.0, 40.0)
    monkeypatch.setenv("VEG_MIN_FREE_GB", "0")
    assert guard.disk_ok()[0] is True
    monkeypatch.delenv("VEG_MIN_FREE_GB")
    assert guard.disk_ok() == (True, 30.0, 20.0)


def test_disk_check_path_from_env(monkeypatch):
    seen = []
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda path: seen.append(path) or Usage(1, 1, 50e9))
    monkeypatch.setenv("VEG_DISK_CHECK_PATH", "/data/x")
    guard.free_gb()
    monkeypatch.delenv("VEG_DISK_CHECK_PATH")
    guard.free_gb()
    assert seen == ["/data/x", "/tmp"]


def test_low_disk_space_message():
    err = guard.LowDiskSpace(30.04, 40.0, "/data/x")
    assert str(err) == "Low disk space: 30.0 GB free < 40.0 GB at /data/x"


def test_tenant_budget_counts_recent_subscription_downloads(monkeypatch):
    monkeypatch.delenv("VEG_MAX_NEW_SCENES_PER_PASS", raising=False)
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 15
    now = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)
    assert guard.tenant_budget(db, "acme", now) == 5
    db.query.return_value.filter.return_value.scalar.return_value = 35
    assert guard.tenant_budget(db, "acme", now) == 0
    monkeypatch.setenv("VEG_MAX_NEW_SCENES_PER_PASS", "50")
    assert guard.tenant_budget(db, "acme", now) == 15
