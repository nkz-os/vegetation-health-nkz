"""Code must only read attributes VegetationJob actually declares.

`job.sensing_date` returned a 500 on every campaign delete with cascade, and the
same line sat in the bulk-delete path. VegetationJob has no such column — the
acquisition date is written into its `result` payload. SQLAlchemy raises
AttributeError at runtime, and nothing caught it before the request handler.

Present since PR #17; it surfaced only when someone first deleted a campaign.
"""
import re
from pathlib import Path

from app.models import VegetationJob

APP = Path(__file__).resolve().parents[1] / "app"


def _declared_columns():
    return set(VegetationJob.__table__.columns.keys())


def test_sensing_date_is_not_a_column_on_the_job():
    """Pin the premise: if it ever becomes one, this test should be deleted."""
    assert "sensing_date" not in _declared_columns()


def test_no_module_reads_sensing_date_off_a_job():
    hits = []
    for path in sorted(APP.rglob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if re.search(r"\bjob\.sensing_date\b", line):
                hits.append(f"{path.relative_to(APP)}:{i}: {line.strip()[:80]}")
    assert not hits, (
        "VegetationJob has no sensing_date column; read it from job.result:\n  "
        + "\n  ".join(hits)
    )
