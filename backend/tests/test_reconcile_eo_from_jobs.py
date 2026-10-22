"""Contract for the EOProduct reconciler.

The reconciler republishes statistics that are already in the database, so
its job is to select the right rows and hand the writer the shape it
expects. Both halves are pinned here: the SQL that picks the rows is read
from source (a mocked session would accept any query), and the publish
arguments are asserted against a fake writer.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reconcile_eo_from_jobs.py"


def _load(monkeypatch, rows, publisher):
    """Import the script with its session factory and writer replaced."""
    spec = importlib.util.spec_from_file_location("reconcile_eo_from_jobs", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    session = MagicMock()
    session.execute.return_value.fetchall.return_value = rows
    monkeypatch.setattr(mod, "SessionLocal", lambda: session)
    monkeypatch.setattr(mod, "upsert_eo_index", publisher)
    return mod, session


def _row(**kw):
    base = dict(
        tenant_id="t1",
        entity_id="urn:ngsi-ld:AgriParcel:p1",
        index_type="NDVI",
        sensing_date="2026-09-12",
        statistics={"mean": 0.32, "valid_pixels": 27802},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_publishes_each_job_with_the_writers_argument_shape(monkeypatch):
    calls = []

    def publisher(**kwargs):
        calls.append(kwargs)
        return "urn:ngsi-ld:EOProduct:t1:p1:2026-09-12"

    mod, _ = _load(monkeypatch, [_row(), _row(index_type="NDRE")], publisher)
    monkeypatch.setattr(sys, "argv", ["x", "--tenants", "t1"])
    assert mod.main() == 0

    assert [c["index_type"] for c in calls] == ["NDVI", "NDRE"]
    assert calls[0]["sensing_date"] == date(2026, 9, 12)
    assert calls[0]["parcel_id"] == "urn:ngsi-ld:AgriParcel:p1"
    # Copernicus reports valid_pixels; the writer reads pixel_count.
    assert calls[0]["statistics"]["pixel_count"] == 27802


def test_explicit_pixel_count_is_not_overwritten(monkeypatch):
    calls = []
    mod, _ = _load(
        monkeypatch,
        [_row(statistics={"mean": 0.1, "valid_pixels": 5, "pixel_count": 9})],
        lambda **kw: calls.append(kw) or "id",
    )
    monkeypatch.setattr(sys, "argv", ["x"])
    assert mod.main() == 0
    assert calls[0]["statistics"]["pixel_count"] == 9


def test_a_failed_publish_is_reported_as_a_nonzero_exit(monkeypatch):
    mod, _ = _load(monkeypatch, [_row()], lambda **kw: None)
    monkeypatch.setattr(sys, "argv", ["x"])
    assert mod.main() == 1


def test_dry_run_writes_nothing(monkeypatch):
    def publisher(**kwargs):  # pragma: no cover - must never run
        raise AssertionError("dry-run must not publish")

    mod, _ = _load(monkeypatch, [_row()], publisher)
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run"])
    assert mod.main() == 0


@pytest.mark.parametrize(
    "clause",
    [
        "status = 'completed'",          # half-written statistics must stay out
        "deleted_at IS NULL",            # a deleted job must not resurrect an index
        "NOT LIKE :sar",                 # SAR has its own publisher and shape
        "entity_id IS NOT NULL",         # no parcel, no EOProduct to merge into
    ],
)
def test_selection_is_constrained(clause):
    """Read from source: a mocked session accepts any query, so assert the text."""
    assert clause in _SCRIPT.read_text()


def test_the_script_is_shipped_in_the_image():
    """A reconciler that is not in the container cannot reconcile anything.

    The Dockerfile used to copy scripts/run_migrations.py by name, so every
    other operational script was absent from the image even though each one
    documents itself as running inside the container.
    """
    dockerfile = (_SCRIPT.parent.parent / "Dockerfile").read_text()
    assert "backend/scripts ./scripts" in dockerfile
