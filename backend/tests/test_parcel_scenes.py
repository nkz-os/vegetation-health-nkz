"""Pure rules of the per-parcel scene status."""
from datetime import datetime, timedelta, timezone

from app.services.parcel_scenes import is_seen, resolve_status

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def test_done_is_sticky():
    assert resolve_status("done", "failed") == "done"
    assert resolve_status("done", "no_valid_pixels") == "done"


def test_no_valid_pixels_only_from_pending():
    assert resolve_status("pending", "no_valid_pixels") == "no_valid_pixels"
    assert resolve_status(None, "no_valid_pixels") == "no_valid_pixels"
    assert resolve_status("cloud_skipped", "no_valid_pixels") == "cloud_skipped"
    assert resolve_status("failed", "no_valid_pixels") == "failed"


def test_incoming_wins_otherwise():
    assert resolve_status(None, "pending") == "pending"
    assert resolve_status("failed", "pending") == "pending"
    assert resolve_status("pending", "done") == "done"


def test_is_seen():
    assert is_seen("done", 0, NOW - timedelta(days=30), NOW)
    assert is_seen("cloud_skipped", 0, NOW - timedelta(days=30), NOW)
    assert is_seen("no_valid_pixels", 0, NOW - timedelta(days=30), NOW)
    assert is_seen("pending", 1, NOW - timedelta(hours=1), NOW)
    assert not is_seen("pending", 1, NOW - timedelta(hours=49), NOW)
    assert is_seen("pending", 3, NOW - timedelta(hours=49), NOW)
    assert not is_seen("failed", 2, NOW, NOW)
    assert is_seen("failed", 3, NOW, NOW)
