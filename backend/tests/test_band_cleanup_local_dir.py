"""Regression: the local Sen2Res/band processing dir must be freed once the
last vegetation index for a scene is computed.

Bug (2026-06-24): /tmp/vegetation_processing/<tenant>/<scene> accumulated
unbounded (12-30 GB) because it was only "cleaned" by Celery child recycling
(max_tasks_per_child) — which recycles the worker PROCESS, not the pod, so it
never frees pod /tmp. Disk pressure -> CrashLoopBackOff.
"""
from unittest.mock import MagicMock, patch

from app.tasks import processing_tasks as pt


def test_local_processing_dir_removed_on_last_calc(tmp_path):
    d = tmp_path / "scene-dir"
    d.mkdir()
    (d / "B08.tif").write_bytes(b"x")
    fake_r = MagicMock()
    fake_r.decr.return_value = 0          # last calculation for the scene
    fake_r.hgetall.return_value = {}      # no bucket meta -> skip MinIO branch
    with patch.object(pt, "_get_redis", return_value=fake_r):
        cleaned = pt._decrement_and_cleanup_bands("scene-prod-1", local_processing_dir=d)
    assert cleaned is True
    assert not d.exists()


def test_local_processing_dir_kept_when_calcs_remain(tmp_path):
    d = tmp_path / "scene-dir"
    d.mkdir()
    fake_r = MagicMock()
    fake_r.decr.return_value = 2          # other index calcs still pending
    with patch.object(pt, "_get_redis", return_value=fake_r):
        pt._decrement_and_cleanup_bands("scene-prod-1", local_processing_dir=d)
    assert d.exists()                     # must NOT race-delete shared bands


def test_decrement_without_local_dir_is_noop_for_fs(tmp_path):
    """Back-compat: callers that pass no dir still work (no crash)."""
    fake_r = MagicMock()
    fake_r.decr.return_value = 0
    fake_r.hgetall.return_value = {}
    with patch.object(pt, "_get_redis", return_value=fake_r):
        assert pt._decrement_and_cleanup_bands("scene-prod-1") is True
