"""One scene, every parcel: the planner, the download and the calculation work per parcel."""
import os
import sys
import uuid
from datetime import date
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
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

import app.tasks.download_tasks as dt
import app.tasks.processing_tasks as pt
import app.tasks.scheduler as sched
from app.models import VegetationJob, VegetationScene
from app.services import parcel_scenes

TENANT = "acme"
PARCEL_B = "urn:ngsi-ld:AgriParcel:parcel-b"
SCENE = "S2A_TEST_SCENE"
GEOM = {"type": "Polygon", "coordinates": [[[-1.6, 42.8], [-1.5, 42.8], [-1.5, 42.9], [-1.6, 42.9], [-1.6, 42.8]]]}


def _query_stub(model_results):
    """db.query(Model) -> chain whose first()/all() return model_results[Model]."""
    def _query(*models):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        value = model_results.get(models[0])
        q.first.return_value = value
        q.all.return_value = [value] if value is not None else []
        return q
    return _query


# ---- planner ---------------------------------------------------------------

def _run_planner(seen):
    sub = MagicMock()
    sub.id = uuid.uuid4()
    sub.tenant_id = TENANT
    sub.entity_id = PARCEL_B
    sub.status = "active"
    sub.index_types = ["NDVI"]
    sub.geometry = object()
    db = MagicMock()
    db.query.side_effect = _query_stub({sched.VegetationSubscription: sub})
    client = MagicMock()
    client.search_scenes.return_value = [{"id": SCENE, "sensing_date": "2026-11-01T10:50:00Z", "cloud_cover": 5}]
    client.search_s1_scenes.return_value = []
    shape = MagicMock()
    shape.bounds = (-1.6, 42.8, -1.5, 42.9)
    shape.geom_type = "Polygon"
    shape.__geo_interface__ = GEOM
    with patch.object(sched, "get_db_session", return_value=iter([db])), \
         patch("geoalchemy2.shape.to_shape", return_value=shape), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient", return_value=client), \
         patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
               return_value={"client_id": "i", "client_secret": "s"}), \
         patch.object(sched.parcel_scenes, "scenes_seen", return_value=seen) as seen_mock, \
         patch.object(sched.parcel_scenes, "record") as record, \
         patch.object(sched, "download_sentinel2_scene") as download:
        sched.check_and_process_entity.run(str(sub.id), "2026-11-01", "2026-11-10")
    return db, seen_mock, record, download


def test_scheduler_enqueues_scene_already_downloaded_for_other_parcel():
    db, seen_mock, record, download = _run_planner(set())
    assert seen_mock.call_args.args[1:4] == (TENANT, PARCEL_B, [SCENE])
    added = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], VegetationJob)]
    assert len(added) == 1 and added[0].entity_id == PARCEL_B and added[0].job_type == "download"
    assert download.delay.call_count == 1
    record.assert_called_once()
    args, kwargs = record.call_args
    assert args == (TENANT, PARCEL_B, SCENE, parcel_scenes.PENDING)
    assert kwargs["sensing_date"] == date(2026, 11, 1) and kwargs["new_attempt"] is True


def test_scheduler_skips_scene_seen_for_this_parcel():
    _, _, record, download = _run_planner({SCENE})
    download.delay.assert_not_called()
    record.assert_not_called()


# ---- download --------------------------------------------------------------

def _run_download(*, existing_scene, local_cloud=80.0, params_extra=None):
    job = MagicMock()
    job.id = uuid.uuid4()
    params = {"scene_id": SCENE, "bbox": [-1.6, 42.8, -1.5, 42.9], "bounds": GEOM,
              "entity_id": PARCEL_B, **(params_extra or {})}
    db = MagicMock()
    db.query.side_effect = _query_stub({VegetationJob: job, VegetationScene: existing_scene})
    client = MagicMock()
    client.get_scene_item.return_value = {
        "id": SCENE, "sensing_date": "2026-11-01", "cloud_cover": 5, "geometry": GEOM,
    }
    with patch.object(dt, "get_db_session", return_value=iter([db])), \
         patch.object(dt, "CopernicusDataSpaceClient", return_value=client), \
         patch.object(dt, "get_copernicus_credentials_with_fallback", return_value=None), \
         patch("app.services.scl_validation.compute_local_cloud_pct", return_value=local_cloud), \
         patch.object(dt.parcel_scenes, "record") as record, \
         patch.object(dt.download_sentinel2_scene, "update_state"):
        dt.download_sentinel2_scene.run(str(job.id), TENANT, params)
    return db, job, record


def test_cloud_skip_keeps_existing_valid_scene_row():
    existing = MagicMock()
    existing.is_valid = True
    existing.bands = {"B04": "x.tif"}
    db, job, record = _run_download(existing_scene=existing)
    assert not [c for c in db.add.call_args_list if isinstance(c.args[0], VegetationScene)]
    assert existing.is_valid is True and existing.bands == {"B04": "x.tif"}
    args, kwargs = record.call_args
    assert args == (TENANT, PARCEL_B, SCENE, parcel_scenes.CLOUD_SKIPPED)
    assert kwargs["parcel_cloud_pct"] == 80.0 and kwargs["job_id"] == job.id
    job.mark_completed.assert_called_once()


def test_cloud_skip_creates_invalid_row_when_none_exists():
    db, _, record = _run_download(existing_scene=None)
    added = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], VegetationScene)]
    assert len(added) == 1 and added[0].is_valid is False and added[0].bands is None
    assert record.call_args.args[3] == parcel_scenes.CLOUD_SKIPPED


def test_cloud_skip_without_entity_records_nothing():
    _, _, record = _run_download(existing_scene=None, params_extra={"entity_id": None})
    record.assert_not_called()


def test_download_success_records_pending_and_failure_records_failed():
    job = MagicMock()
    job.id = uuid.uuid4()
    cache = MagicMock()
    cache.quality_flags = {}
    cache.get_band_path.side_effect = lambda b: f"scenes/{SCENE}/{b}.tif"
    existing = MagicMock()
    existing.quality_flags = {}
    existing.id = uuid.uuid4()
    params = {"scene_id": SCENE, "bbox": [-1.6, 42.8, -1.5, 42.9], "bounds": GEOM, "entity_id": PARCEL_B,
              "local_cloud_threshold": 100}
    storage = MagicMock()
    storage.file_exists.return_value = True
    client = MagicMock()
    client.get_scene_item.return_value = {
        "id": SCENE, "sensing_date": "2026-11-01", "cloud_cover": 5, "geometry": GEOM,
    }

    def run(model_results):
        db = MagicMock()
        db.query.side_effect = _query_stub(model_results)
        with patch.object(dt, "get_db_session", return_value=iter([db])), \
             patch.object(dt, "CopernicusDataSpaceClient", return_value=client), \
             patch.object(dt, "get_copernicus_credentials_with_fallback", return_value=None), \
             patch.object(dt, "create_storage_service", return_value=storage), \
             patch("app.services.scl_validation.compute_local_cloud_pct", return_value=0.0), \
             patch("app.services.usage_tracker.UsageTracker"), \
             patch.object(dt.parcel_scenes, "record") as record, \
             patch.object(dt.download_sentinel2_scene, "update_state"):
            try:
                dt.download_sentinel2_scene.run(str(job.id), TENANT, params)
                error = None
            except Exception as exc:  # the task re-raises after recording
                error = exc
        return record, error

    record, error = run({VegetationJob: job, dt.GlobalSceneCache: cache, VegetationScene: existing})
    assert error is None
    record.assert_called_once_with(TENANT, PARCEL_B, SCENE, parcel_scenes.PENDING, job_id=job.id)

    client.get_scene_item.side_effect = RuntimeError("catalog down")
    record, error = run({VegetationJob: job})
    assert isinstance(error, RuntimeError)
    record.assert_called_once_with(TENANT, PARCEL_B, SCENE, parcel_scenes.FAILED, job_id=job.id)


def test_failed_download_survives_a_failing_record():
    job = MagicMock()
    job.id = uuid.uuid4()
    db = MagicMock()
    db.query.side_effect = _query_stub({VegetationJob: job})
    client = MagicMock()
    client.get_scene_item.side_effect = RuntimeError("catalog down")
    params = {"scene_id": SCENE, "bbox": [-1.6, 42.8, -1.5, 42.9], "entity_id": PARCEL_B}
    with patch.object(dt, "get_db_session", return_value=iter([db])), \
         patch.object(dt, "CopernicusDataSpaceClient", return_value=client), \
         patch.object(dt, "get_copernicus_credentials_with_fallback", return_value=None), \
         patch("app.services.usage_tracker.UsageTracker"), \
         patch.object(dt.parcel_scenes, "record", side_effect=ValueError("boom")), \
         patch.object(dt.download_sentinel2_scene, "update_state"):
        with pytest.raises(RuntimeError, match="catalog down"):  # original error, not the record one
            dt.download_sentinel2_scene.run(str(job.id), TENANT, params)


# ---- entity -> scene link --------------------------------------------------

def test_entity_scene_filter_uses_download_job_scene_not_job_id():
    clause = parcel_scenes.downloaded_for_entity(TENANT, "parcel-a", completed_only=True)
    sql = str(clause.compile(dialect=postgresql.dialect()))
    assert "vegetation_scenes.job_id" not in sql
    assert "coalesce" in sql.lower() and "vegetation_scenes.scene_id" in sql
    assert "vegetation_jobs.job_type" in sql and "vegetation_jobs.status" in sql


def test_entity_scene_filter_survives_job_id_overwrite():
    url = os.getenv("ZONING_PG_URL")
    if not url:
        pytest.skip("ZONING_PG_URL not set")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(url)
    job_a, job_b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_scenes, vegetation_jobs")
            conn.exec_driver_sql(
                "CREATE TABLE vegetation_jobs (id uuid PRIMARY KEY, tenant_id text, entity_id text, "
                "job_type text, status text, parameters jsonb, result jsonb)")
            conn.exec_driver_sql(
                "CREATE TABLE vegetation_scenes (id uuid PRIMARY KEY, tenant_id text, scene_id text, "
                "is_valid boolean, job_id uuid)")
            conn.exec_driver_sql(
                "INSERT INTO vegetation_jobs VALUES (%s, 'acme', 'parcel-a', 'download', 'completed', "
                "'{\"scene_id\": \"S1\"}', NULL), (%s, 'acme', 'parcel-b', 'download', 'completed', "
                "NULL, '{\"scene_product_id\": \"S1\"}')", (job_a, job_b))
            conn.exec_driver_sql(
                "INSERT INTO vegetation_jobs VALUES (%s, 'acme', 'parcel-d', 'download', 'completed', "
                "'{\"scene_id\": \"S1\"}', '{\"skipped_due_to_clouds\": true}')", (str(uuid.uuid4()),))
            # S1.job_id was overwritten by parcel-b's download
            conn.exec_driver_sql(
                "INSERT INTO vegetation_scenes VALUES (%s, 'acme', 'S1', true, %s)",
                (str(uuid.uuid4()), job_b))
        session = sessionmaker(bind=engine)()
        try:
            def ids(entity):
                return session.query(VegetationScene.id).filter(
                    VegetationScene.tenant_id == "acme", VegetationScene.is_valid == True,  # noqa: E712
                    parcel_scenes.downloaded_for_entity("acme", entity, completed_only=True),
                ).all()
            assert len(ids("parcel-a")) == 1
            assert len(ids("parcel-b")) == 1
            assert len(ids("parcel-c")) == 0
            assert len(ids("parcel-d")) == 0  # clouded for parcel-d: never computed there
        finally:
            session.close()
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_scenes, vegetation_jobs")
        engine.dispose()


# ---- calculation -----------------------------------------------------------

def _verdict(published, stats, *, entity=PARCEL_B, index="NDVI", composite=False):
    job = MagicMock()
    job.entity_id = entity
    scene = MagicMock()
    scene.scene_id = SCENE
    scene.sensing_date = date(2026, 11, 1)
    with patch.object(pt.parcel_scenes, "record") as record:
        pt._record_parcel_verdict(TENANT, job, index, composite, scene, published, stats)
    return record


def test_calc_marks_done_or_no_valid_pixels():
    ok = {"mean": 0.5, "pixel_count": 10}
    empty = {"mean": 0.0, "pixel_count": 0}
    record = _verdict(True, ok)
    assert record.call_args.args == (TENANT, PARCEL_B, SCENE, parcel_scenes.DONE)
    record = _verdict(False, empty)
    assert record.call_args.args == (TENANT, PARCEL_B, SCENE, parcel_scenes.NO_VALID_PIXELS)
    _verdict(False, ok).assert_not_called()                      # broker failure: not annotated
    _verdict(True, ok, composite=True).assert_not_called()
    _verdict(True, ok, index="CUSTOM").assert_not_called()
    _verdict(True, ok, entity=None).assert_not_called()


def test_calc_idempotency_exit_records_done():
    job = MagicMock()
    job.id = uuid.uuid4()
    job.entity_id = PARCEL_B
    job.parameters = {"index_type": "NDVI", "scene_id": SCENE}
    job.start_date = None
    job.end_date = None
    scene = MagicMock()
    scene.id = uuid.uuid4()
    scene.scene_id = SCENE
    scene.sensing_date = date(2026, 11, 1)
    db = MagicMock()
    db.query.side_effect = _query_stub({VegetationJob: job, VegetationScene: scene})
    with patch.object(pt, "get_db_session", return_value=iter([db])), \
         patch.object(pt, "_check_idempotency", return_value=True), \
         patch.object(pt, "_decrement_and_cleanup_bands"), \
         patch.object(pt.parcel_scenes, "record") as record, \
         patch.object(pt.calculate_vegetation_index, "update_state"):
        pt.calculate_vegetation_index.run(str(job.id), TENANT, SCENE, "NDVI", None, None, None)
    record.assert_called_once_with(
        TENANT, PARCEL_B, SCENE, parcel_scenes.DONE, sensing_date=date(2026, 11, 1),
    )
