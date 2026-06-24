"""Worker-boot sweep of orphaned per-scene processing dirs (disk-pressure fix)."""
from app.celery_app import _sweep_processing_root


def test_sweep_removes_processing_root(tmp_path):
    root = tmp_path / "vegetation_processing"
    (root / "tenantA" / "scene1").mkdir(parents=True)
    (root / "tenantA" / "scene1" / "B08.tif").write_bytes(b"x")
    assert _sweep_processing_root(str(root)) is True
    assert not root.exists()


def test_sweep_noop_when_absent(tmp_path):
    root = tmp_path / "does-not-exist"
    assert _sweep_processing_root(str(root)) is False
