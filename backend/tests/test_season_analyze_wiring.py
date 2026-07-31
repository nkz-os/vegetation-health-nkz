"""Tests for the async 'Analyze in season' pipeline BYOK wiring +
crop_season_id propagation (`_dispatch_analyze_for_parcel`).

Covers:
  - Copernicus-eligible indices (NDVI) computed INLINE via the selector,
    persisted as completed calculate_index jobs BOUND to crop_season_id,
    with NO scene download for them.
  - Local-only indices (NDRE) routed to the download pipeline, whose download
    job carries crop_season_id in its parameters (so the worker propagates it
    to the child calc jobs).
  - Mixed request: NDVI → Copernicus, NDRE → local, both in one call.
  - All-Copernicus request → early return, scene search never touched.
  - Sentinel Hub NOT usable → every index falls to local; selector/quota untouched.
  - Over-quota → the index is skipped (no local substitute), quota protected.

Imports the real app (mirrors test_calculate_routing) — DB/selector/quota and
the external Copernicus/Orion clients are patched per-test. The async helper is
driven with asyncio.run (no pytest-asyncio dependency needed).
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

with patch("app.database.init_db"):
    import app.api.scenes as scenes

from app.engines.base import IndexResult

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


def _index_result(idx="NDVI", data_fidelity="sentinel_hub"):
    return IndexResult(
        index_type=idx, sensing_date=date(2026, 7, 20),
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


# ---------------------------------------------------------------------------
# All-Copernicus → inline compute, season-bound, no scene download
# ---------------------------------------------------------------------------

def test_all_copernicus_indices_persist_with_season_and_skip_download():
    db = _make_db()
    selector = _selector(sh_usable=True, results=[_index_result("NDVI")])
    quota = MagicMock()
    quota.check_and_reserve.return_value = True
    ensure_mock = MagicMock(return_value="p/NDVI.tif")
    # Mock actually mutates the job's result (simulating eager generation).
    def _ensure(db_, job):
        job.result["raster_path"] = "p/NDVI.tif"
        job.result["raster_pending"] = False
        return "p/NDVI.tif"

    with _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch("app.services.copernicus_raster.ensure_copernicus_raster", _ensure), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient") as CopCls, \
         patch.object(scenes, "download_sentinel2_scene") as dl:
        out = _run(db=db, indices=["NDVI"], engine_selector=selector)

    # Copernicus computed inline, one unit reserved, kept.
    selector.compute_indices.assert_awaited_once()
    quota.check_and_reserve.assert_called_once_with(TENANT)
    quota.release.assert_not_called()
    # No local pipeline at all.
    CopCls.assert_not_called()
    dl.delay.assert_not_called()
    # Early return carries the Copernicus job.
    assert len(out["job_ids"]) == 1
    assert out["windows"] == 0
    # Persisted job is season-bound + labeled copernicus, per-window shape.
    persisted = db.add.call_args.args[0]
    assert str(persisted.crop_season_id) == SEASON
    assert persisted.result["engine"] == "copernicus"
    assert persisted.result["data_fidelity"] == "sentinel_hub"
    assert persisted.result.get("raster_pending") is False  # eager latest cleared it
    assert persisted.result.get("raster_path") is not None    # eager latest set it


# ---------------------------------------------------------------------------
# Mixed: NDVI → Copernicus, NDRE → local (download carries season)
# ---------------------------------------------------------------------------

def test_mixed_indices_split_copernicus_and_local():
    db = _make_db()
    selector = _selector(sh_usable=True, results=[_index_result("NDVI")])
    quota = MagicMock()
    quota.check_and_reserve.return_value = True
    ensure_mock = MagicMock(return_value="p/NDVI.tif")
    def _ensure(db_, job):
        job.result["raster_path"] = "p/NDVI.tif"
        job.result["raster_pending"] = False
        return "p/NDVI.tif"

    cop_client = MagicMock()
    cop_client.search_scenes.return_value = [
        {"id": "S2_1", "sensing_date": "2026-07-10", "cloud_cover": 5},
    ]

    with _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch("app.services.copernicus_raster.ensure_copernicus_raster", _ensure), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient", return_value=cop_client), \
         patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
               return_value={"client_id": "x", "client_secret": "y"}), \
         patch("app.services.temporal_utils.group_scenes_into_windows",
               return_value=[{"scenes": [{"id": "S2_1", "cloud_cover": 5}]}]), \
         patch.object(scenes, "download_sentinel2_scene") as dl:
        dl.delay.return_value = MagicMock(id="celery-1")
        out = _run(db=db, indices=["NDVI", "NDRE"], engine_selector=selector)

    # NDVI ran on Copernicus (season-bound completed job, per-window).
    selector.compute_indices.assert_awaited_once()
    cop_job = db.add.call_args_list[0].args[0]
    assert cop_job.result["index_type"] == "NDVI"
    assert str(cop_job.crop_season_id) == SEASON

    # NDRE went to the local download pipeline; the download job carries the
    # season in BOTH the FK and its parameters (so the worker propagates it).
    dl.delay.assert_called_once()
    download_jobs = db.add_all.call_args.args[0]
    assert len(download_jobs) == 1
    dj = download_jobs[0]
    assert dj.job_type == "download"
    assert str(dj.crop_season_id) == SEASON
    assert dj.parameters["crop_season_id"] == SEASON
    assert dj.parameters["calculate_indices"] == ["NDRE"]  # only the local index

    # Merged response has both jobs.
    assert len(out["job_ids"]) == 2


# ---------------------------------------------------------------------------
# Sentinel Hub not usable → everything local, selector/quota untouched
# ---------------------------------------------------------------------------

def test_sh_not_usable_routes_all_local_with_season():
    db = _make_db()
    selector = _selector(sh_usable=False)
    cop_client = MagicMock()
    cop_client.search_scenes.return_value = [
        {"id": "S2_1", "sensing_date": "2026-07-10", "cloud_cover": 5},
    ]

    with _orion_patch(), \
         patch.object(scenes, "SatelliteQuota") as QuotaCls, \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient", return_value=cop_client), \
         patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
               return_value={"client_id": "x", "client_secret": "y"}), \
         patch("app.services.temporal_utils.group_scenes_into_windows",
               return_value=[{"scenes": [{"id": "S2_1", "cloud_cover": 5}]}]), \
         patch.object(scenes, "download_sentinel2_scene") as dl:
        dl.delay.return_value = MagicMock(id="celery-2")
        out = _run(db=db, indices=["NDVI", "NDRE"], engine_selector=selector)

    selector.is_sentinel_hub_usable.assert_called_once_with(TENANT)
    selector.compute_indices.assert_not_awaited()
    QuotaCls.assert_not_called()
    # Both indices go local in one download job, season-bound.
    dj = db.add_all.call_args.args[0][0]
    assert dj.parameters["calculate_indices"] == ["NDVI", "NDRE"]
    assert dj.parameters["crop_season_id"] == SEASON
    assert str(dj.crop_season_id) == SEASON
    assert len(out["job_ids"]) == 1


# ---------------------------------------------------------------------------
# Over-quota → Copernicus index skipped (no local substitute), quota protected
# ---------------------------------------------------------------------------

def test_over_quota_skips_copernicus_index():
    db = _make_db()
    selector = _selector(sh_usable=True)
    quota = MagicMock()
    quota.check_and_reserve.return_value = False

    with _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient") as CopCls, \
         patch.object(scenes, "download_sentinel2_scene") as dl:
        out = _run(db=db, indices=["NDVI"], engine_selector=selector)

    # Reserved-and-refused: selector NEVER called (shared credential protected).
    quota.check_and_reserve.assert_called_once_with(TENANT)
    selector.compute_indices.assert_not_awaited()
    quota.release.assert_not_called()
    # NDVI was NOT downgraded to local — nothing to do → empty run.
    CopCls.assert_not_called()
    dl.delay.assert_not_called()
    assert out["job_ids"] == []


# ---------------------------------------------------------------------------
# No engine_selector supplied (legacy caller) → all local, no crash
# ---------------------------------------------------------------------------

def test_no_selector_falls_back_to_local_pipeline():
    db = _make_db()
    cop_client = MagicMock()
    cop_client.search_scenes.return_value = [
        {"id": "S2_1", "sensing_date": "2026-07-10", "cloud_cover": 5},
    ]

    with _orion_patch(), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient", return_value=cop_client), \
         patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
               return_value={"client_id": "x", "client_secret": "y"}), \
         patch("app.services.temporal_utils.group_scenes_into_windows",
               return_value=[{"scenes": [{"id": "S2_1", "cloud_cover": 5}]}]), \
         patch.object(scenes, "download_sentinel2_scene") as dl:
        dl.delay.return_value = MagicMock(id="celery-3")
        out = _run(db=db, indices=["NDVI"], engine_selector=None)

    dj = db.add_all.call_args.args[0][0]
    assert dj.parameters["calculate_indices"] == ["NDVI"]
    assert str(dj.crop_season_id) == SEASON
    assert len(out["job_ids"]) == 1


# ---------------------------------------------------------------------------
# Per-window Copernicus jobs: latest eager raster, older lazy
# ---------------------------------------------------------------------------

def test_copernicus_creates_per_window_jobs_latest_has_raster():
    """Two NDVI windows → two jobs, latest has raster_path, older has raster_pending."""
    db = _make_db()
    from datetime import date as _d
    results = [
        _index_result("NDVI", data_fidelity="sentinel_hub"),
        IndexResult(index_type="NDVI", sensing_date=_d(2026, 7, 5),
                     mean=0.5, std=0.1, min=0.0, max=1.0,
                     p10=0.3, p90=0.8, valid_pixels=100, total_pixels=120,
                     data_fidelity="sentinel_hub"),
    ]
    selector = _selector(sh_usable=True, results=results)
    quota = MagicMock()
    quota.check_and_reserve.return_value = True

    ensured = []
    def _ensure(db_, job):
        job.result["raster_path"] = "p/NDVI.tif"
        job.result["raster_pending"] = False
        ensured.append(job)
        return "p/NDVI.tif"

    with _orion_patch(), \
         patch.object(scenes, "SatelliteQuota", return_value=quota), \
         patch("app.services.copernicus_raster.ensure_copernicus_raster", _ensure), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient"), \
         patch.object(scenes, "download_sentinel2_scene"):
        out = _run(db=db, indices=["NDVI"], engine_selector=selector)

    created = [c.args[0] for c in db.add.call_args_list]
    ndvi_jobs = [j for j in created if (j.result or {}).get("index_type") == "NDVI"]
    assert len(ndvi_jobs) == 2                     # one per window
    latest = max(ndvi_jobs, key=lambda j: j.result["sensing_date"])
    older  = min(ndvi_jobs, key=lambda j: j.result["sensing_date"])
    assert latest.result["raster_path"] == "p/NDVI.tif"     # eager
    assert older.result.get("raster_pending") is True        # lazy
    assert older.result.get("raster_path") is None
    assert all(str(j.crop_season_id) == SEASON for j in ndvi_jobs)
    assert all(j.result.get("geometry") for j in ndvi_jobs)  # geometry stored for lazy gen
