"""Tests for the 'Analyze in season' pipeline (`_dispatch_analyze_for_parcel`),
its Copernicus worker leg (`persist_copernicus_indices`, `analyze_copernicus`)
and crop_season_id propagation.

Covers:
  - Copernicus-eligible indices are QUEUED as one `copernicus_analyze` job —
    never computed inside the request (that blew the gateway's 30 s timeout).
  - Queue down → quota refunded, indices routed to the local pipeline.
  - Sentinel Hub NOT usable / no selector → every index local, season-bound.
  - Over-quota → the index is skipped (no local substitute), quota protected.
  - Worker leg: ONE statistical call for all indices, per-window jobs with an
    eager latest raster, one EOProduct per result, broker failures tolerated,
    quota refunded on degradation or failure.
  - Task: success, local fallback on Copernicus failure, failed when both fail.

Imports the real app (mirrors test_calculate_routing) — DB/selector/quota and
the external Copernicus/Orion clients are patched per-test. Async helpers are
driven with asyncio.run (no pytest-asyncio dependency needed).
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

with patch("app.database.init_db"):
    from app.api import scenes

from app.engines.base import IndexResult
from app.models import VegetationJob
from app.services import copernicus_analyze as cop_service
from app.tasks import copernicus_analyze_task as cop_task

TENANT = "test-tenant-season"
ENTITY = "urn:ngsi-ld:AgriParcel:p-season"
SEASON = str(uuid4())
GEOM = {
    "type": "Polygon",
    "coordinates": [[[-1.6, 42.8], [-1.5, 42.8], [-1.5, 42.9], [-1.6, 42.9], [-1.6, 42.8]]],
}


def _make_db():
    """MagicMock Session; refresh assigns an id like a real INSERT returning PK."""
    db = MagicMock()

    def _refresh(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()

    db.refresh.side_effect = _refresh
    return db


def _index_result(idx="NDVI", data_fidelity="sentinel_hub", sensing=date(2026, 7, 20)):
    return IndexResult(
        index_type=idx, sensing_date=sensing,
        mean=0.62, std=0.08, min=0.1, max=0.9,
        p10=0.4, p90=0.8, valid_pixels=1200, total_pixels=1500,
        data_fidelity=data_fidelity,
    )


def _orion_patch():
    """Patch scenes.OrionClient so get_entity yields a parcel with a geometry."""
    inst = MagicMock()
    inst.get_entity = AsyncMock(return_value={"location": {"value": GEOM}})
    inst.close = AsyncMock()
    cls = MagicMock(return_value=inst)
    return patch.object(scenes, "OrionClient", cls)


def _selector(sh_usable=True, results=None):
    sel = MagicMock()
    sel.is_sentinel_hub_usable = MagicMock(return_value=sh_usable)
    sel.compute_indices = AsyncMock(return_value=results or [_index_result("NDVI")])
    return sel


def _quota(reserve=True):
    quota = MagicMock()
    if isinstance(reserve, list):
        quota.check_and_reserve.side_effect = reserve
    else:
        quota.check_and_reserve.return_value = reserve
    return quota


def _local_patches(dl_id="celery-local"):
    """Patch the local pipeline's external calls; returns (context list, dl mock)."""
    cop_client = MagicMock()
    cop_client.search_scenes.return_value = [
        {"id": "S2_1", "sensing_date": "2026-07-10", "cloud_cover": 5},
    ]
    dl = MagicMock()
    dl.delay.return_value = MagicMock(id=dl_id)
    ctx = [
        patch("app.services.copernicus_client.CopernicusDataSpaceClient", return_value=cop_client),
        patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
              return_value={"client_id": "x", "client_secret": "y"}),
        patch("app.services.temporal_utils.group_scenes_into_windows",
              return_value=[{"scenes": [{"id": "S2_1", "cloud_cover": 5}]}]),
        patch("app.tasks.download_sentinel2_scene", dl),
    ]
    return ctx, dl, cop_client


def _run(**kwargs):
    defaults = dict(
        db=_make_db(),
        tenant_id=TENANT,
        entity_id=ENTITY,
        user_id="u-1",
        custom_formula_specs=[],
        start_date="2026-07-01",
        end_date="2026-07-31",
        local_cloud_threshold=None,
        crop_season_id=SEASON,
    )
    defaults.update(kwargs)
    return asyncio.run(scenes._dispatch_analyze_for_parcel(**defaults))


def _enter(stack, ctxs):
    return [stack.enter_context(c) for c in ctxs]


# ---------------------------------------------------------------------------
# Handler: Copernicus indices are queued, never computed in the request
# ---------------------------------------------------------------------------

def test_copernicus_indices_are_queued_not_computed_inline():
    from contextlib import ExitStack

    db = _make_db()
    selector = _selector(sh_usable=True)
    quota = _quota(True)
    local_ctx, dl, _ = _local_patches()

    with ExitStack() as stack, _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch.object(scenes, "analyze_copernicus") as task:
        _enter(stack, local_ctx)
        task.delay.return_value = MagicMock(id="celery-cop")
        out = _run(db=db, indices=["NDVI", "NDRE"], engine_selector=selector)

    selector.compute_indices.assert_not_awaited()
    assert quota.check_and_reserve.call_count == 2
    quota.release.assert_not_called()
    dl.delay.assert_not_called()

    task.delay.assert_called_once()
    job_id, tenant, params = task.delay.call_args.args
    parent = db.add.call_args.args[0]
    assert parent.job_type == "copernicus_analyze"
    assert str(parent.crop_season_id) == SEASON
    assert parent.celery_task_id == "celery-cop"
    assert job_id == str(parent.id) and tenant == TENANT
    assert params["indices"] == ["NDVI", "NDRE"]
    assert params["geometry"] == GEOM
    assert params["start_date"] == "2026-07-01" and params["end_date"] == "2026-07-31"
    assert params["crop_season_id"] == SEASON

    assert out["job_ids"] == [str(parent.id)]
    assert out["indices"] == ["NDVI", "NDRE"]
    assert out["windows"] == 0


def test_enqueue_failure_refunds_and_routes_local():
    from contextlib import ExitStack

    db = _make_db()
    selector = _selector(sh_usable=True)
    quota = _quota(True)
    local_ctx, _, _ = _local_patches()

    with ExitStack() as stack, _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch.object(scenes, "analyze_copernicus") as task:
        _enter(stack, local_ctx)
        task.delay.side_effect = ConnectionError("broker down")
        out = _run(db=db, indices=["NDVI"], engine_selector=selector)

    assert quota.release.call_count == 1
    parent = db.add.call_args_list[0].args[0]
    assert parent.status == "failed"
    dj = db.add_all.call_args.args[0][0]
    assert dj.parameters["calculate_indices"] == ["NDVI"]
    assert dj.parameters["crop_season_id"] == SEASON
    assert out["job_ids"] == [str(dj.id)]


def test_sh_not_usable_routes_all_local_with_season():
    from contextlib import ExitStack

    db = _make_db()
    selector = _selector(sh_usable=False)
    local_ctx, _, _ = _local_patches("celery-2")

    with ExitStack() as stack, _orion_patch(), \
         patch.object(scenes, "SatelliteQuota") as QuotaCls, \
         patch.object(scenes, "analyze_copernicus") as task:
        _enter(stack, local_ctx)
        out = _run(db=db, indices=["NDVI", "NDRE"], engine_selector=selector)

    selector.is_sentinel_hub_usable.assert_called_once_with(TENANT)
    QuotaCls.assert_not_called()
    task.delay.assert_not_called()
    dj = db.add_all.call_args.args[0][0]
    assert dj.parameters["calculate_indices"] == ["NDVI", "NDRE"]
    assert dj.parameters["crop_season_id"] == SEASON
    assert str(dj.crop_season_id) == SEASON
    assert len(out["job_ids"]) == 1


def test_over_quota_skips_copernicus_index():
    db = _make_db()
    selector = _selector(sh_usable=True)
    quota = _quota(False)

    with _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient") as CopCls, \
         patch.object(scenes, "analyze_copernicus") as task:
        out = _run(db=db, indices=["NDVI"], engine_selector=selector)

    quota.check_and_reserve.assert_called_once_with(TENANT)
    quota.release.assert_not_called()
    task.delay.assert_not_called()
    CopCls.assert_not_called()
    assert out["job_ids"] == []


def test_quota_exhaustion_stops_at_reserved_prefix():
    db = _make_db()
    selector = _selector(sh_usable=True)
    quota = _quota([True, False])

    with _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch.object(scenes, "analyze_copernicus") as task:
        task.delay.return_value = MagicMock(id="celery-cop")
        out = _run(db=db, indices=["NDVI", "SAVI"], engine_selector=selector)

    assert task.delay.call_args.args[2]["indices"] == ["NDVI"]
    assert out["indices"] == ["NDVI"]


def test_no_selector_falls_back_to_local_pipeline():
    from contextlib import ExitStack

    db = _make_db()
    local_ctx, _, _ = _local_patches("celery-3")

    with ExitStack() as stack, _orion_patch():
        _enter(stack, local_ctx)
        out = _run(db=db, indices=["NDVI"], engine_selector=None)

    dj = db.add_all.call_args.args[0][0]
    assert dj.parameters["calculate_indices"] == ["NDVI"]
    assert str(dj.crop_season_id) == SEASON
    assert len(out["job_ids"]) == 1


def test_local_pipeline_without_scenes_is_404():
    from contextlib import ExitStack

    local_ctx, _, cop_client = _local_patches()
    cop_client.search_scenes.return_value = []

    with ExitStack() as stack, _orion_patch():
        _enter(stack, local_ctx)
        with pytest.raises(HTTPException) as exc:
            _run(indices=["NDVI"], engine_selector=None)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Worker leg: persist_copernicus_indices
# ---------------------------------------------------------------------------

def _persist(db, selector, quota, indices, ensure=None):
    def _default_ensure(db_, job):
        job.result["raster_path"] = f"p/{job.result['index_type']}.tif"
        job.result["raster_pending"] = False
        return job.result["raster_path"]

    with patch("app.services.copernicus_raster.ensure_copernicus_raster", ensure or _default_ensure):
        return asyncio.run(cop_service.persist_copernicus_indices(
            db,
            engine_selector=selector,
            quota=quota,
            tenant_id=TENANT,
            entity_id=ENTITY,
            geometry=GEOM,
            date_range=(date(2026, 7, 1), date(2026, 7, 31)),
            indices=indices,
            season_uuid=SEASON,
            user_id="u-1",
        ))


def test_worker_uses_one_statistical_call_for_all_indices():
    db = _make_db()
    selector = _selector(results=[_index_result("NDVI"), _index_result("SAVI"), _index_result("GNDVI")])
    quota = _quota()
    with patch.object(cop_service, "upsert_eo_index"):
        ids = _persist(db, selector, quota, ["NDVI", "SAVI", "GNDVI"])

    selector.compute_indices.assert_awaited_once()
    assert selector.compute_indices.await_args.kwargs["index_types"] == ["NDVI", "SAVI", "GNDVI"]
    quota.release.assert_not_called()
    assert len(ids) == 3


def test_worker_creates_per_window_jobs_latest_has_raster():
    db = _make_db()
    selector = _selector(results=[
        _index_result("NDVI", sensing=date(2026, 7, 20)),
        _index_result("NDVI", sensing=date(2026, 7, 5)),
    ])
    with patch.object(cop_service, "upsert_eo_index"):
        _persist(db, selector, _quota(), ["NDVI"])

    jobs = [c.args[0] for c in db.add.call_args_list]
    assert len(jobs) == 2
    latest = max(jobs, key=lambda j: j.result["sensing_date"])
    older = min(jobs, key=lambda j: j.result["sensing_date"])
    assert latest.result["raster_path"] == "p/NDVI.tif"       # eager
    assert older.result.get("raster_pending") is True          # lazy
    assert older.result.get("raster_path") is None
    assert all(str(j.crop_season_id) == SEASON for j in jobs)
    assert all(j.result.get("geometry") for j in jobs)
    assert all(j.result["engine"] == "copernicus" for j in jobs)


def test_worker_publishes_an_eoproduct_per_result():
    db = _make_db()
    with patch.object(cop_service, "upsert_eo_index") as eo:
        _persist(db, _selector(), _quota(), ["NDVI"])

    eo.assert_called_once()
    kwargs = eo.call_args.kwargs
    assert kwargs["index_type"] == "NDVI"
    assert kwargs["parcel_id"].startswith("urn:ngsi-ld:AgriParcel:")
    assert kwargs["statistics"]["pixel_count"] == kwargs["statistics"]["valid_pixels"]


def test_worker_survives_a_broker_failure():
    db = _make_db()
    with patch.object(cop_service, "upsert_eo_index", side_effect=RuntimeError("broker down")):
        ids = _persist(db, _selector(), _quota(), ["NDVI"])
    assert ids, "the job must survive a broker failure"


def test_worker_refunds_quota_when_selector_degrades():
    db = _make_db()
    selector = _selector(results=[_index_result("NDVI", data_fidelity="degraded_fallback")])
    quota = _quota()
    with patch.object(cop_service, "upsert_eo_index"):
        _persist(db, selector, quota, ["NDVI"])
    quota.release.assert_called_once_with(TENANT)
    assert db.add.call_args.args[0].result["engine"] == "local_processing"


def test_worker_refunds_quota_and_reraises_on_failure():
    db = _make_db()
    selector = _selector()
    selector.compute_indices = AsyncMock(side_effect=RuntimeError("SH down"))
    quota = _quota()
    with pytest.raises(RuntimeError):
        _persist(db, selector, quota, ["NDVI", "SAVI"])
    assert quota.release.call_count == 2


# ---------------------------------------------------------------------------
# Task: analyze_copernicus
# ---------------------------------------------------------------------------

PARAMS = {
    "indices": ["NDVI"],
    "entity_id": ENTITY,
    "geometry": GEOM,
    "bbox": [-1.6, 42.8, -1.5, 42.9],
    "start_date": "2026-07-01",
    "end_date": "2026-07-31",
    "crop_season_id": SEASON,
    "local_cloud_threshold": None,
}


def _run_task(job, persist, local=None):
    db = _make_db()
    db.query.return_value.filter.return_value.first.return_value = job
    with patch.object(cop_task, "get_db_session", return_value=iter([db])), \
         patch.object(cop_task, "_engine_selector", return_value=MagicMock()), \
         patch("app.services.satellite_quota.SatelliteQuota"), \
         patch("app.services.copernicus_analyze.persist_copernicus_indices", persist), \
         patch("app.services.local_analyze.dispatch_local_pipeline",
               local or MagicMock(return_value={"job_ids": ["dl-1"], "windows": 1, "scenes_found": 1})) as lp:
        cop_task.analyze_copernicus.run(str(job.id), TENANT, PARAMS)
    return lp


def _parent():
    return VegetationJob(id=uuid4(), tenant_id=TENANT, job_type="copernicus_analyze",
                         entity_id=ENTITY, parameters=PARAMS, created_by="u-1")


def test_task_completes_with_copernicus_job_ids():
    job = _parent()
    lp = _run_task(job, AsyncMock(return_value=["j1", "j2"]))
    assert job.status == "completed"
    assert job.result == {"engine": "copernicus", "indices": ["NDVI"], "job_ids": ["j1", "j2"]}
    lp.assert_not_called()


def test_task_routes_local_when_copernicus_fails():
    job = _parent()
    lp = _run_task(job, AsyncMock(side_effect=RuntimeError("SH down")))
    lp.assert_called_once()
    kwargs = lp.call_args.kwargs
    assert kwargs["indices"] == ["NDVI"]
    assert kwargs["include_sar"] is False
    assert kwargs["crop_season_id"] == SEASON
    assert job.status == "completed"
    assert job.result["engine"] == "local_fallback"
    assert job.result["job_ids"] == ["dl-1"]
    assert "SH down" in job.result["copernicus_error"]


def test_task_fails_when_local_fallback_also_fails():
    from app.services.local_analyze import AnalyzeDispatchError

    job = _parent()
    _run_task(
        job,
        AsyncMock(side_effect=RuntimeError("SH down")),
        MagicMock(side_effect=AnalyzeDispatchError(404, "No scenes found in the selected date range")),
    )
    assert job.status == "failed"
    assert "No scenes found" in job.error_message


# ---------------------------------------------------------------------------
# SAR-only local work must not download Sentinel-2 scenes
# ---------------------------------------------------------------------------

def test_sar_only_local_work_skips_sentinel2_downloads():
    """Optical indices all on Copernicus + SAR → only Sentinel-1 is downloaded."""
    from contextlib import ExitStack

    db = _make_db()
    selector = _selector(sh_usable=True)
    quota = _quota(True)
    local_ctx, dl, cop_client = _local_patches()
    cop_client.search_s1_scenes.return_value = [
        {"id": "S1_1", "sensing_date": "2026-07-12"},
    ]
    s1 = MagicMock()
    s1.delay.return_value = MagicMock(id="celery-s1")

    with ExitStack() as stack, _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch.object(scenes, "analyze_copernicus") as task, \
         patch("app.tasks.sar_tasks.download_sentinel1_scene", s1):
        _enter(stack, local_ctx)
        task.delay.return_value = MagicMock(id="celery-cop")
        out = _run(db=db, indices=["NDVI"], engine_selector=selector, include_sar=True)

    cop_client.search_scenes.assert_not_called()
    dl.delay.assert_not_called()
    s1.delay.assert_called_once()
    sar_job = next(c.args[0] for c in db.add.call_args_list if c.args[0].job_type == "download_sar")
    assert str(sar_job.id) in out["job_ids"]
    assert out["windows"] == 0
