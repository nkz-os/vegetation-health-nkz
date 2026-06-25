"""calculate_vegetation_index must release the Redis idempotency lock on
failure using the SAME key it claimed.

Prod bug (2026-06-24): the release path derived the sensing_date from
job.result/parameters (empty at failure time) instead of the scene's sensing
date used to claim, so the lock leaked → every retry skipped as
'completed/raster_path=None' → no map layers.
"""
import os
import sys
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "shapely", "shapely.geometry",
    "shapely.ops", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import pytest

import app.tasks.processing_tasks as pt
from app.models import VegetationJob, VegetationScene


def test_release_idempotency_on_failure_uses_claimed_key():
    job_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    entity = "urn:ngsi-ld:AgriParcel:da36ccd2-85d2-4c76-b552-c5c835a987c1"

    job = MagicMock()
    job.id = job_id
    job.tenant_id = "montiko"
    job.entity_id = entity
    job.parameters = {"index_type": "NDVI", "scene_id": str(scene_id)}
    job.result = None
    job.start_date = None
    job.end_date = None

    scene = MagicMock()
    scene.id = scene_id
    scene.scene_id = "S2B_TEST"
    scene.sensing_date = date(2026, 6, 21)
    scene.bands = {}  # no bands → compute aborts AFTER the idempotency claim

    def _make_q(first_val):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.first.return_value = first_val
        q.all.return_value = [first_val] if first_val is not None else []
        return q

    def _query(model):
        if model is VegetationJob:
            return _make_q(job)
        if model is VegetationScene:
            return _make_q(scene)
        return _make_q(None)

    mock_db = MagicMock()
    mock_db.query.side_effect = _query

    with patch.object(pt, "get_db_session", return_value=iter([mock_db])), \
         patch.object(pt, "_check_idempotency", return_value=False) as claim, \
         patch.object(pt, "_release_idempotency") as release, \
         patch.object(pt.calculate_vegetation_index, "update_state"):
        with pytest.raises(Exception):
            pt.calculate_vegetation_index.run(
                str(job_id), "montiko", str(scene_id), "NDVI", None, None, None
            )

    # Claimed with (tenant, entity, idempotency_key=index_type, sensing_date)
    claim.assert_called_once_with("montiko", entity, "NDVI", "2026-06-21")
    # Released with the SAME key on failure
    release.assert_called_once_with("montiko", entity, "NDVI", "2026-06-21")
