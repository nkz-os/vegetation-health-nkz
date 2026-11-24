"""Optical writers: EOSatelliteImagery next to the legacy EOProduct, behind a per-tenant switch.

The broker is a dict with ``options=update`` semantics (attributes sent overwrite,
attributes not sent stay), so a "replace" that forgets the leftovers fails here.
"""
import asyncio
import copy
import logging
import os
import sys
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import numpy as np
import pytest
from nkz_platform_sdk import parse_eo_entities

import app.tasks.processing_tasks as pt
from app.models import VegetationJob, VegetationScene
from app.services import eo_satimg_optical as so
from app.services import fiware_integration as fi
from app.services.eo_satellite_imagery import Family

TENANT = "acme"
PARCEL = "urn:ngsi-ld:AgriParcel:parcel-1"
ZONE = "urn:ngsi-ld:AgriManagementZone:parcel-1:run-1:L1"
DAY = date(2026, 6, 21)
PID_A = "S2A_MSIL2A_20260621T105031_N0511_R051_T30TWM_20260621T130000"
PID_B = "S2B_MSIL2A_20260621T110621_N0511_R094_T30TWN_20260621T160012"
GEOM = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]}
FOOT = {"type": "Polygon", "coordinates": [[[-1.0, -1.0], [2.0, -1.0], [2.0, 2.0], [-1.0, -1.0]]]}
SCENE_UUID = uuid.UUID("22222222-2222-2222-2222-222222222222")

PARCEL_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s2-indices:2026-06-21"
STATS = {"mean": 0.61, "min": 0.2, "max": 0.9, "std": 0.1, "pixel_count": 40}


class Store:
    """Broker stand-in: one dict per entity, upsert = options=update."""

    def __init__(self):
        self.entities = {}
        self.upserts = []
        self.deleted = []
        self.pruned = []
        self.reads = 0

    def get(self, tenant, eid):
        self.reads += 1
        e = self.entities.get(eid)
        return copy.deepcopy(e) if e is not None else None

    def upsert(self, tenant, entity):
        self.upserts.append(copy.deepcopy(entity))
        self.entities.setdefault(entity["id"], {}).update(copy.deepcopy(entity))
        return entity["id"]

    def delete(self, tenant, eid):
        self.deleted.append(eid)
        self.entities.pop(eid, None)
        return True

    def delete_attr(self, tenant, eid, attr):
        self.pruned.append(attr)
        self.entities.get(eid, {}).pop(attr, None)
        return "deleted"


@pytest.fixture
def store(monkeypatch):
    s = Store()
    monkeypatch.setattr(fi, "get_eo_satellite_imagery", s.get)
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", s.upsert)
    monkeypatch.setattr(fi, "delete_eo_satellite_imagery", s.delete)
    monkeypatch.setattr(fi, "delete_eo_attribute", s.delete_attr)
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)
    monkeypatch.setenv("VEGETATION_COG_BUCKET", "bucket")
    return s


def _analytics(entity):
    return {i["name"]: i["value"] for i in entity["analytics"]["value"]}


def _scene(pid=PID_B, scene_uuid=SCENE_UUID, when=datetime(2026, 6, 21, 11, 6, 21, tzinfo=timezone.utc)):
    s = MagicMock()
    s.id = scene_uuid
    s.scene_id = pid
    s.sensing_date = DAY
    s.acquisition_datetime = when
    s.footprint = FOOT
    s.cloud_coverage = "12.5"
    s.storage_bucket = "bucket"
    s.bands = {"B04": "b/B04.tif", "B08": "b/B08.tif", "SCL": "b/SCL.tif"}
    return s


def _write_scene(pid, index, mean, fraction=None, *, extra=None, folder=None):
    """One single-scene parcel write straight through the optical writer."""
    scene = _scene(pid=pid, scene_uuid=uuid.uuid5(uuid.NAMESPACE_URL, pid))
    take = so.scene_take(scene, raster_folder=folder)
    analytics = so.index_analytics(index, {**STATS, "mean": mean},
                                   interior=({"valid_fraction": fraction, "valid_pixels": 30, "mask": "core",
                                              "mean": mean} if fraction is not None else None))
    analytics["pixelCount"] = 40
    analytics.update(extra or {})
    return so.write_optical(TENANT, so.parcel_target(PARCEL, GEOM), take, analytics,
                            index_type=index, interior_fraction=fraction)


# ── calculate_vegetation_index harness ─────────────────────────────────────

class _FakeProcessor:
    def __init__(self, band_paths=None, bbox=None, radiometry=None):
        self.band_data = {"SCL": np.zeros((4, 4))}
        self.band_meta = {"crs": "EPSG:32630", "height": 4, "width": 4, "transform": None}

    def load_bands(self, bands):
        pass

    def calculate_ndvi(self):
        arr = np.full((4, 4), 0.6, dtype=np.float32)
        arr.flat[:4] = np.nan
        return arr

    def create_geometry_mask(self, geojson):
        return np.zeros((4, 4), dtype=bool)

    def calculate_statistics(self, arr, mask=None):
        v = arr[np.isfinite(arr)]
        return {"mean": float(v.mean()), "min": float(v.min()), "max": float(v.max()),
                "std": float(v.std()), "pixel_count": int(v.size)}

    def vectorize_index(self, *a, **k):
        return None

    def save_index_raster(self, arr, path):
        return path


def _q(value):
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.first.return_value = value
    q.all.return_value = [value] if value is not None else []
    return q


def _calc(composite=False, existing=None, real_legacy=False):
    job = MagicMock()
    job.id = uuid.uuid4()
    job.entity_id = PARCEL
    job.parameters = {"index_type": "NDVI", "bounds": GEOM}
    job.start_date, job.end_date = (date(2026, 6, 1), DAY) if composite else (None, None)
    scene = _scene()
    if composite:
        scene.sensing_date = date(2026, 6, 18)
        scene.acquisition_datetime = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)
    db = MagicMock()
    db.query.side_effect = lambda model: _q(
        job if model is VegetationJob else scene if model is VegetationScene else None)
    with patch.object(pt, "get_db_session", return_value=iter([db])), \
         patch.object(pt, "_check_idempotency", return_value=False), \
         patch.object(pt, "_decrement_and_cleanup_bands"), \
         patch.object(pt, "create_storage_service", return_value=MagicMock()), \
         patch.object(pt, "VegetationIndexProcessor", _FakeProcessor), \
         patch.object(pt, "l2a_radiometry", return_value=None), \
         patch.object(pt, "interior_mask", MagicMock(return_value=(np.ones((4, 4), dtype=bool), "core"))), \
         patch.object(pt, "parcel_mask", MagicMock(return_value=np.ones((4, 4), dtype=bool))), \
         patch.object(pt, "existing_interior_observation", return_value=existing), \
         patch.object(pt, "upsert_eo_index", **({"wraps": fi.upsert_eo_index} if real_legacy
                                                  else {"return_value": "urn:ngsi-ld:EOProduct:x"})) as legacy, \
         patch.object(pt.parcel_scenes, "record"), \
         patch("app.services.window_crop.crop_bands_to_window", lambda paths, *a, **k: paths), \
         patch("app.services.zone_products.publish_zone_indices") as zones, \
         patch.object(pt.calculate_vegetation_index, "update_state"):
        pt.calculate_vegetation_index.run(str(job.id), TENANT, scene_id=None if composite else str(scene.id),
                                          index_type="NDVI")
    return job, legacy, zones


# ── Step 1 tests (brief) ───────────────────────────────────────────────────

def test_single_scene_writes_s2_indices_entity_when_enabled(store):
    job, legacy, _ = _calc()
    entity = store.entities[PARCEL_ID]
    assert entity["type"] == "EOSatelliteImagery"
    assert entity["analysisType"]["value"] == "vegetation-indices"
    assert entity["sensingDate"]["value"] == "2026-06-21T11:06:21Z"
    assert entity["productID"]["value"] == PID_B
    assert entity["productURL"]["value"] == (
        f"https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a/items/{PID_B}")
    assert entity["platformName"]["value"] == "S2B"
    assert entity["orbitNumber"]["value"] == 94
    assert entity["operationalMode"]["value"] == "MSI"
    assert entity["productFormat"]["value"] == "SAFE"
    assert entity["productType"]["value"] == "S2MSI2A"
    assert entity["productBbox"]["value"] == FOOT
    assert entity["processedBbox"]["value"] == GEOM
    values = _analytics(entity)
    for name in ("ndvi", "ndvi.min", "ndvi.max", "ndvi.std", "pixelCount", "cloudCoverage",
                 "parcelCloudFraction", "ndvi.interiorValidFraction", "ndvi.interiorMask"):
        assert name in values, name
    assert values["cloudCoverage"] == "12.50"
    folder = f"s3://bucket/{TENANT}/entities/{PARCEL}/scenes/{SCENE_UUID}"
    assert entity["localServerPath"]["value"] == folder
    # One file per index: the legacy rasterUrl names the same object.
    assert legacy.call_args.kwargs["raster_url"] == f"{folder}/ndvi.tif"
    assert job.mark_completed.call_args.args[0]["raster_path"].endswith(f"scenes/{SCENE_UUID}/ndvi.tif")


def test_disabled_tenant_writes_only_old_form(store, monkeypatch):
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", "other-tenant")
    _, legacy, zones = _calc()
    legacy.assert_called_once()
    zones.assert_called_once()
    assert store.entities == {} and store.reads == 0  # no extra broker traffic
    assert zones.call_args.kwargs.get("satimg") is None
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", "")
    assert fi.satimg_enabled(TENANT) is False
    monkeypatch.delenv("EO_SATIMG_WRITE_TENANTS")
    assert fi.satimg_enabled(TENANT) is False
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", " other , *")
    assert fi.satimg_enabled(TENANT) is True


def test_composite_uses_window_end_and_family_s2_composite(store):
    job, legacy, _ = _calc(composite=True)
    eid = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s2-composite:2026-06-21"
    entity = store.entities[eid]
    assert entity["analysisType"]["value"] == "vegetation-indices-composite"
    assert entity["sensingDate"]["value"] == "2026-06-21"  # window end, never the first scene
    assert "2026-06-01" in entity["resultDescription"]["value"]
    assert "2026-06-21" in entity["resultDescription"]["value"]
    assert "productID" not in entity and entity["productType"]["value"] == "S2MSI2A"
    folder = f"s3://bucket/{TENANT}/entities/{PARCEL}/composites/2026-06-01_2026-06-21"
    assert entity["localServerPath"]["value"] == folder
    assert legacy.call_args.kwargs["raster_url"] == f"{folder}/ndvi.tif"
    values = _analytics(entity)
    assert not any("interior" in n for n in values)
    assert "parcelCloudFraction" not in values and "cloudCoverage" not in values
    assert legacy.call_args.kwargs["sensing_date"] == date(2026, 6, 18)  # legacy unchanged


def test_statistical_api_path_has_no_scene_attrs(store):
    from app.engines.base import IndexResult
    from app.services import copernicus_analyze as cop

    result = IndexResult(index_type="NDVI", sensing_date=DAY, mean=0.62, std=0.08, min=0.1, max=0.9,
                         p10=0.4, p90=0.8, valid_pixels=1200, total_pixels=1500, data_fidelity="sentinel_hub")
    selector = MagicMock()

    async def _compute(**kw):
        return [result]

    selector.compute_indices = _compute
    db = MagicMock()
    db.refresh.side_effect = lambda obj: setattr(obj, "id", uuid.uuid4())
    async def _noop(*a, **k):
        return None

    with patch.object(cop, "upsert_eo_index") as legacy, \
         patch("app.services.copernicus_raster.materialize_if_pending", _noop):
        asyncio.run(cop.persist_copernicus_indices(
            db, engine_selector=selector, quota=MagicMock(), tenant_id=TENANT, entity_id=PARCEL,
            geometry=GEOM, date_range=(date(2026, 6, 1), DAY), indices=["NDVI"],
            season_uuid=None, user_id=None))
    legacy.assert_called_once()
    entity = store.entities[PARCEL_ID]
    assert entity["sensingDate"]["value"] == "2026-06-21"
    assert entity["productType"]["value"] == "S2MSI2A"
    for attr in ("productID", "productURL", "productFormat", "platformName", "orbitNumber",
                 "operationalMode", "productBbox", "localServerPath"):
        assert attr not in entity, attr
    values = _analytics(entity)
    assert values["pixelCount"] == "1200" and values["validPixelFraction"] == "0.8000"
    assert "statistical" in entity["resultDescription"]["value"].lower()


def test_reprocess_same_day_keeps_existing_values(store):
    _write_scene(PID_A, "NDVI", 0.6, 0.8)
    _write_scene(PID_A, "EVI", 0.4, 0.8)
    # Reprocess NDVI of the same scene (interior_backfill, rerun): EVI stays in the list.
    assert _write_scene(PID_A, "NDVI", 0.65, 0.85) == so.MERGE
    values = _analytics(store.entities[PARCEL_ID])
    assert values["evi"] == "0.400000" and values["ndvi"] == "0.650000"
    assert values["ndvi.interiorValidFraction"] == "0.8500"


def test_reprocess_through_interior_backfill_merges(store, monkeypatch):
    from app.jobs import interior_backfill as ib

    _write_scene(PID_A, "EVI", 0.4, 0.8)
    scene = _scene(pid=PID_A)
    broker = MagicMock()
    broker.get_product.return_value = {"ndvi": {"type": "Property", "value": 0.61}}
    broker.parcel_geometry.return_value = GEOM
    broker.upsert.return_value = "urn:ngsi-ld:EOProduct:x"
    broker.upsert_satimg = ib.OrionBroker.upsert_satimg.__get__(broker)
    monkeypatch.setattr(ib, "_load_jobs", lambda db, t, parcel=None: [{
        "entity_id": PARCEL, "index_type": "NDVI", "sensing_date": DAY, "statistics": STATS,
        "raster_path": f"{TENANT}/entities/{PARCEL}/scenes/{scene.id}/ndvi.tif", "scene_id": str(scene.id)}])
    monkeypatch.setattr(ib, "interior_from_raster",
                        lambda raw, geom: {"valid_fraction": 0.9, "valid_pixels": 30, "mask": "core", "mean": 0.6})
    report = ib.backfill_tenant(MagicMock(), TENANT, apply=True, reader=lambda p: b"", broker=broker,
                                retire_zeros=False, scene_loader=lambda db, sid: scene)
    assert report.errors == 0 and report.updated == 1
    values = _analytics(store.entities[PARCEL_ID])
    assert values["evi"] == "0.400000" and values["ndvi.interiorValidFraction"] == "0.9000"


def test_best_interior_scene_wins_same_day(store):
    _write_scene(PID_A, "NDVI", 0.6, 0.8)
    _write_scene(PID_A, "EVI", 0.4, 0.8)
    assert _write_scene(PID_B, "NDVI", 0.5, 0.6) == so.SKIP  # worse interior: A stays
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_A and _analytics(entity)["ndvi"] == "0.600000"
    assert _write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE  # better interior: B takes the day
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_B
    assert "evi" not in _analytics(entity)
    assert _write_scene(PID_B, "EVI", 0.45, 0.9) == so.MERGE
    assert set(n for n in _analytics(store.entities[PARCEL_ID]) if "." not in n) >= {"ndvi", "evi"}


def test_zone_products_write_zone_entity_with_parcel_relation(store):
    from app.services import zone_products as zp

    zone = {"zone_id": "run-1-L1", "zone_urn": ZONE, "zone_version": "run-1", "geometry": GEOM,
            "relation": zp.CURRENT_ZONE_RELATION, "level": "L1", "run_id": "run-1"}
    take = so.scene_take(_scene(pid=PID_A), raster_folder="s3://bucket/x/scenes/y")
    stats = {**STATS, "valid_fraction": 0.75}
    with patch.object(zp, "fetch_zone_targets", return_value=[zone]), \
         patch.object(zp, "zone_statistics", return_value=stats), \
         patch.object(fi, "upsert_eo_zone_index", return_value="urn:ngsi-ld:EOProduct:z") as legacy:
        zp.publish_zone_indices(TENANT, PARCEL, "NDVI", MagicMock(), np.zeros((2, 2)), DAY,
                                cloud_cover="3.5", satimg=so.ZoneWrite(take, so.CREATE))
    legacy.assert_called_once()
    eid = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:zone:parcel-1:run-1:L1:s2-indices:2026-06-21"
    entity = store.entities[eid]
    assert entity["hasAgriParcel"]["object"] == PARCEL
    assert entity["hasAgriManagementZone"]["object"] == ZONE
    assert "hasAgriParcelZone" not in entity
    assert entity["processedBbox"]["value"] == GEOM
    assert "localServerPath" not in entity  # zones keep no raster
    values = _analytics(entity)
    assert values["validPixelFraction"] == "0.7500" and values["cloudCoverage"] == "3.50"
    assert not any("interior" in n for n in values) and "parcelCloudFraction" not in values
    # Legacy only (switch on but the parcel lost the day): no new zone entity is touched.
    store.entities.clear()
    with patch.object(zp, "fetch_zone_targets", return_value=[zone]), \
         patch.object(zp, "zone_statistics", return_value=stats), \
         patch.object(fi, "upsert_eo_zone_index", return_value="z") as legacy:
        zp.publish_zone_indices(TENANT, PARCEL, "NDVI", MagicMock(), np.zeros((2, 2)), DAY, satimg=None)
    legacy.assert_called_once()
    assert store.entities == {}


def test_old_form_unchanged(store, monkeypatch):
    from tests.fake_orion import FakeAsyncOrion

    sent = []

    class Recorder(FakeAsyncOrion):
        async def upsert_entities_batch(self, entities):
            sent.extend(copy.deepcopy(entities))
            return await super().upsert_entities_batch(entities)

    # The real legacy writer runs inside the calculation; what reaches the broker is the EOProduct.
    payloads, calls = [], []
    for composite in (False, True):
        runs = []
        for switch in ("", TENANT):
            monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", switch)
            store.entities.clear()
            sent.clear()
            with patch.object(fi, "OrionClient", Recorder):
                _, legacy, _ = _calc(composite=composite, real_legacy=True)
            runs.append(([e for e in sent if e.get("type") == "EOProduct"], legacy.call_args))
        assert runs[0][0] and runs[0][0] == runs[1][0], composite
        assert runs[0][1] == runs[1][1], composite
        payloads.append(runs[0][0])
    assert payloads[0][0]["type"] == "EOProduct"
    assert (store.entities != {}) is True  # the last run had the switch on and wrote the new form


def test_same_day_other_scene_replaces_whole_list(store):
    # A from before the interior measure: nothing to defend, B replaces it whole.
    _write_scene(PID_A, "NDVI", 0.6, folder="s3://bucket/a")
    _write_scene(PID_A, "EVI", 0.4, folder="s3://bucket/a")
    assert "contentInformation" in store.entities[PARCEL_ID]
    assert _write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE
    entity = store.entities[PARCEL_ID]
    assert PARCEL_ID not in store.deleted  # a replace never deletes the entity
    assert entity["productID"]["value"] == PID_B and entity["platformName"]["value"] == "S2B"
    assert "evi" not in _analytics(entity)
    for leftover in ("localServerPath", "contentInformation", "storageFormat"):
        assert leftover not in entity, leftover


def test_local_path_replaces_statistical_api_entity(store):
    take = so.no_scene_take(DAY)
    for idx in ("NDVI", "SAVI"):
        a = so.index_analytics(idx, STATS)
        a["pixelCount"] = 40
        so.write_optical(TENANT, so.parcel_target(PARCEL, GEOM), take, a, index_type=idx)
    assert "productID" not in store.entities[PARCEL_ID]
    assert {"ndvi", "savi"} <= set(_analytics(store.entities[PARCEL_ID]))
    assert _write_scene(PID_A, "NDVI", 0.6, 0.8) == so.REPLACE
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_A and "savi" not in _analytics(entity)
    # And the Statistical API never displaces a scene afterwards.
    a = so.index_analytics("SAVI", STATS)
    a["pixelCount"] = 40
    assert so.write_optical(TENANT, so.parcel_target(PARCEL, GEOM), take, a, index_type="SAVI") == so.SKIP
    assert "savi" not in _analytics(store.entities[PARCEL_ID])


def test_delete_paths_remove_both_forms_during_dual_write(store, monkeypatch):
    from app.api import jobs as jobs_api
    from app.api import monitoring_periods as mp
    from app.models.crop_seasons import VegetationMonitoringPeriod
    from app.services import zone_products as zp

    def seed():
        store.entities.clear()
        _write_scene(PID_A, "NDVI", 0.6, 0.8)
        _write_scene(PID_A, "EVI", 0.4, 0.8)
        zone_take = so.scene_take(_scene(pid=PID_A))
        za = so.index_analytics("NDVI", STATS)
        za["pixelCount"] = 40
        so.write_optical(TENANT, so.zone_target(PARCEL, ZONE, GEOM), zone_take, za, index_type="NDVI",
                         follow=so.CREATE)

    zone = {"zone_id": "run-1-L1", "zone_urn": ZONE, "relation": zp.CURRENT_ZONE_RELATION}
    zone_eid = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:zone:parcel-1:run-1:L1:s2-indices:2026-06-21"
    job = MagicMock()
    job.id = uuid.uuid4()
    job.entity_id = PARCEL
    job.scene_id = PID_A  # the mocked session answers every query with the job: its STAC id resolves the scene
    job.parameters = {"index_type": "NDVI"}
    job.result = {"index_type": "NDVI", "sensing_date": DAY.isoformat(), "is_composite": False,
                  "scene_id": str(SCENE_UUID)}
    # The switch only gates writes: a tenant switched off still has its entities cleaned.
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)

    for run in ("bulk", "period", "zones"):
        seed()
        monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", "")
        with patch.object(fi, "delete_eo_index") as legacy, \
             patch.object(zp, "fetch_zone_targets", return_value=[zone]):
            if run == "bulk":
                db = MagicMock()
                db.query.return_value = _q(job)
                asyncio.run(jobs_api.bulk_delete_jobs(jobs_api.BulkDeleteRequest(ids=[str(job.id)]),
                                                      current_user={"tenant_id": TENANT}, db=db))
            elif run == "period":
                db = MagicMock()
                db.query.side_effect = lambda model: _q(MagicMock() if model is VegetationMonitoringPeriod else job)
                asyncio.run(mp.delete_monitoring_period(PARCEL, str(uuid.uuid4()), cascade=True,
                                                        current_user={"tenant_id": TENANT}, db=db))
            else:
                zp.delete_zone_products(TENANT, PARCEL, DAY.isoformat(), "NDVI",
                                         job_result={**job.result, "scene_product_id": PID_A})
        monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)
        assert legacy.called, run
        assert zone_eid not in store.entities, run  # only index of the zone entity: gone
        if run != "zones":
            values = _analytics(store.entities[PARCEL_ID])
            assert "evi" in values and not any(n.split(".")[0] == "ndvi" for n in values), run

    # Removing an index keeps the take's other values and layers; the last one removes the entity.
    assert "contentInformation" not in store.entities[PARCEL_ID]
    assert so.remove_index(TENANT, "parcel", PARCEL, Family.S2_INDICES, DAY, "NDVI")
    assert set(_analytics(store.entities[PARCEL_ID])) >= {"evi", "evi.interiorMask", "pixelCount"}
    assert so.remove_index(TENANT, "parcel", PARCEL, Family.S2_INDICES, DAY, "EVI")
    assert PARCEL_ID not in store.entities
    assert so.remove_index(TENANT, "parcel", PARCEL, Family.S2_INDICES, DAY, "EVI")  # missing = done


def test_composite_job_delete_targets_window_end():
    assert so.job_family_day({"is_composite": True, "sensing_date": "2026-06-18",
                              "date_range": {"start": "2026-06-01", "end": "2026-06-21"}}) == \
        (Family.S2_COMPOSITE, DAY)
    assert so.job_family_day({"sensing_date": "2026-06-21T00:00:00"}) == (Family.S2_INDICES, DAY)
    assert so.job_family_day({}) is None


def test_written_entities_parse_with_sdk_without_warnings(store, caplog):
    _calc()
    _calc(composite=True)
    _write_scene(PID_B, "EVI", 0.4, 0.8)
    so.write_optical(TENANT, so.zone_target(PARCEL, ZONE, GEOM), so.scene_take(_scene()),
                     {**so.index_analytics("NDVI", STATS), "pixelCount": 40, "validPixelFraction": 0.7},
                     index_type="NDVI", follow=so.CREATE)
    so.write_optical(TENANT, so.parcel_target("urn:ngsi-ld:AgriParcel:parcel-2", None), so.no_scene_take(DAY),
                     {**so.index_analytics("NDVI", STATS), "pixelCount": 40}, index_type="NDVI")
    assert len(store.upserts) >= 5
    with caplog.at_level(logging.WARNING, logger="nkz_platform_sdk.eo"):
        observations = parse_eo_entities(store.upserts)
    assert [r for r in caplog.records if r.name.startswith("nkz_platform_sdk.eo")] == []
    assert {o.family for o in observations} == {"s2-indices", "s2-composite"}
    scene_obs = next(o for o in observations if o.product_id == PID_B and o.target == "parcel")
    assert scene_obs.raster_url("ndvi").endswith(f"scenes/{SCENE_UUID}/ndvi.tif")


# ── fail-safe, raster keys, shared rasters ─────────────────────────────────

def test_new_form_failure_never_breaks_the_legacy_write(store, monkeypatch, caplog):
    def boom(tenant, eid):
        raise RuntimeError("broker down")

    monkeypatch.setattr(fi, "get_eo_satellite_imagery", boom)
    with caplog.at_level(logging.ERROR):
        job, legacy, _ = _calc()
    legacy.assert_called_once()
    assert job.mark_completed.call_args.args[0]["published"] is True
    assert store.entities == {}
    text = caplog.text
    assert TENANT in text and PARCEL in text and "s2-indices" in text and "2026-06-21" in text


def test_index_raster_key_names():
    key = pt.index_raster_key("t", PARCEL, "NDRE", job_id="j", scene_uuid="s")
    assert key == f"t/entities/{PARCEL}/scenes/s/ndre.tif"
    key = pt.index_raster_key("t", PARCEL, "NDVI", job_id="j", window=("2026-06-01", "2026-06-21"))
    assert key == f"t/entities/{PARCEL}/composites/2026-06-01_2026-06-21/ndvi.tif"
    assert pt.index_raster_key("t", PARCEL, "CUSTOM", job_id="j", scene_uuid="s").endswith("/scenes/s/CUSTOM.tif")
    assert pt.index_raster_key("t", PARCEL, "CUSTOM", job_id="j",
                               window=("a", "b")).endswith("/composites/j/CUSTOM.tif")
    # Without a parcel there is no window folder of its own: one folder per job, as before.
    assert pt.index_raster_key("t", None, "NDVI", job_id="j",
                               window=("a", "b")) == "t/entities/unknown/composites/j/ndvi.tif"
    # A raster is declared on the new form only when its file follows the analytics-name rule.
    assert so.raster_folder("b", "t/x/scenes/s/ndvi.tif", "NDVI") == "s3://b/t/x/scenes/s"
    assert so.raster_folder("b", "t/x/scenes/s/NDVI.tif", "NDVI") is None
    assert so.raster_folder("b", None, "NDVI") is None


def test_hard_delete_keeps_a_raster_another_job_uses():
    from app.api import parcels

    job = MagicMock()
    job.status = "completed"
    job.job_type = "calculate_index"
    job.result = {"raster_path": "t/x/composites/a_b/ndvi.tif"}
    for others, deleted in ((1, False), (0, True)):
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = others
        with patch.object(parcels, "_delete_raster_safely") as drop:
            parcels._hard_delete_one(db, TENANT, job)
        assert drop.called is deleted


# ── other writers ──────────────────────────────────────────────────────────

def _reconcile(monkeypatch, rows):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "reconcile_eo_from_jobs.py"
    spec = importlib.util.spec_from_file_location("reconcile_eo_from_jobs_satimg", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = rows
    monkeypatch.setattr(mod, "SessionLocal", lambda: session)
    monkeypatch.setattr(mod, "upsert_eo_index", lambda **kw: "urn:ngsi-ld:EOProduct:x")
    monkeypatch.setattr(sys, "argv", ["x", "--tenants", TENANT])
    return mod


def test_reconcile_composite_job_is_dated_by_window_end(store, monkeypatch):
    from types import SimpleNamespace

    base = dict(tenant_id=TENANT, entity_id=PARCEL, index_type="NDVI", statistics={"mean": 0.5, "valid_pixels": 9},
                geometry=GEOM, interior=None, parcel_cloud_fraction=None)
    composite = SimpleNamespace(**base, sensing_date="2026-06-18", start_date=date(2026, 6, 1), end_date=DAY,
                                raster_path=f"{TENANT}/entities/{PARCEL}/composites/2026-06-01_2026-06-21/ndvi.tif",
                                scene_product_id=None)
    # /calculate composites keep their window in the result only: the job columns are empty.
    by_result = SimpleNamespace(**{**base, "index_type": "EVI"}, sensing_date="2026-06-18", start_date=None,
                                end_date=None, window_start="2026-06-01", window_end="2026-06-21",
                                raster_path=None, scene_product_id=PID_A)
    scene = SimpleNamespace(**{**base, "interior": {"valid_fraction": 0.8, "valid_pixels": 7, "mask": "core"}},
                            sensing_date="2026-06-20", start_date=None, end_date=None,
                            raster_path=f"{TENANT}/entities/{PARCEL}/scenes/u/NDVI.tif",  # before the rename
                            scene_product_id=PID_A, scene_sensing_date=date(2026, 6, 20),
                            scene_acquisition_datetime=None, scene_cloud_coverage="N/A",
                            scene_footprint='{"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}')
    assert _reconcile(monkeypatch, [composite, by_result, scene]).main() == 0
    comp = store.entities[f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s2-composite:2026-06-21"]
    assert {"ndvi", "evi"} <= set(_analytics(comp)) and "productID" not in comp
    assert comp["sensingDate"]["value"] == "2026-06-21"
    assert comp["localServerPath"]["value"].endswith("/composites/2026-06-01_2026-06-21")
    single = store.entities[f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s2-indices:2026-06-20"]
    assert single["productID"]["value"] == PID_A and single["productBbox"]["value"]["type"] == "Polygon"
    assert "localServerPath" not in single  # NDVI.tif is not {folder}/ndvi.tif
    values = _analytics(single)
    assert values["ndvi.interiorValidFraction"] == "0.8000" and "cloudCoverage" not in values


def test_historical_baseline_writes_the_window_scene(store):
    from app.tasks import historical_baseline as hb

    class Proc:
        def __init__(self, *a, **k):
            pass

        def calculate_ndvi(self):
            return np.ones((2, 2))

        def create_geometry_mask(self, geom):
            return np.zeros((2, 2), dtype=bool)

        def calculate_statistics(self, arr, mask=None):
            return dict(STATS)

    client = MagicMock()
    client.search_scenes.return_value = [{"id": PID_A, "sensing_date": "2026-06-21",
                                          "datetime": "2026-06-21T10:50:31.024Z", "cloud_cover": 4.25,
                                          "geometry": FOOT}]
    client.download_scene_bands.return_value = {"B04": "x", "B08": "y", "SCL": "z"}
    with patch("app.services.processor.VegetationIndexProcessor", Proc), \
         patch("app.services.radiometry.l2a_radiometry", return_value=None), \
         patch.object(hb, "upsert_eo_index") as legacy:
        assert hb._process_window(TENANT, PARCEL, GEOM, [0, 0, 1, 1], client, date(2026, 6, 15), DAY,
                                  "NDVI", 30.0, ["B04", "B08", "SCL"])
    legacy.assert_called_once()
    entity = store.entities[PARCEL_ID]
    assert entity["sensingDate"]["value"] == "2026-06-21T10:50:31Z"
    assert entity["productID"]["value"] == PID_A and entity["orbitNumber"]["value"] == 51
    assert _analytics(entity)["cloudCoverage"] == "4.25" and "localServerPath" not in entity


def test_removing_an_index_drops_its_layer(store):
    _write_scene(PID_A, "NDVI", 0.6, 0.8, folder="s3://bucket/a")
    _write_scene(PID_A, "EVI", 0.4, 0.8, folder="s3://bucket/a")
    layers = [c["layer_name"] for c in store.entities[PARCEL_ID]["contentInformation"]["value"]]
    assert layers == ["ndvi", "evi"]
    assert so.remove_index(TENANT, "parcel", PARCEL, Family.S2_INDICES, DAY, "NDVI")
    entity = store.entities[PARCEL_ID]
    assert "contentInformation" not in entity  # one layer left
    assert entity["localServerPath"]["value"] == "s3://bucket/a" and entity["productID"]["value"] == PID_A


def test_missing_values_never_overwrite_and_zero_pixels_write_nothing(store):
    _write_scene(PID_A, "NDVI", 0.6, 0.8, extra={"parcelCloudFraction": 0.1})
    assert _write_scene(PID_A, "NDVI", 0.62, 0.8, extra={"parcelCloudFraction": np.float32("nan")}) == so.MERGE
    assert _analytics(store.entities[PARCEL_ID])["parcelCloudFraction"] == "0.1000"
    before = copy.deepcopy(store.entities)
    assert _write_scene(PID_A, "EVI", 0.4, 0.8, extra={"pixelCount": 0}) == so.SKIP
    assert store.entities == before


def test_scene_that_lost_the_day_in_the_old_form_only_completes_its_own_entity(store):
    winner = (0.99, "s3://bucket/x/scenes/11111111-1111-1111-1111-111111111111/NDVI.tif")
    _, legacy, zones = _calc(existing=winner)
    legacy.assert_not_called()
    zones.assert_not_called()
    assert store.entities == {}  # never created from the losing scene
    _write_scene(PID_A, "EVI", 0.4)  # another take without a fraction to defend
    _calc(existing=winner)
    assert store.entities[PARCEL_ID]["productID"]["value"] == PID_A
    store.entities.clear()
    _write_scene(PID_B, "EVI", 0.4, 0.5)  # its own take already holds the day
    _, legacy, zones = _calc(existing=winner)
    legacy.assert_not_called()
    assert {"evi", "ndvi"} <= set(_analytics(store.entities[PARCEL_ID]))
    assert zones.call_args.kwargs["legacy"] is False and zones.call_args.kwargs["satimg"] is not None


def test_deleting_another_takes_job_keeps_the_stored_values(store):
    folder = f"s3://bucket/{TENANT}/entities/{PARCEL}/scenes/{SCENE_UUID}"
    _write_scene(PID_B, "NDVI", 0.6, 0.8, folder=folder)
    before = copy.deepcopy(store.entities)
    single = {"sensing_date": DAY.isoformat()}
    assert so.remove_job_index(TENANT, PARCEL, {**single, "scene_id": str(uuid.uuid4())}, "NDVI")  # other scene
    assert so.remove_job_index(TENANT, PARCEL, single, "NDVI")  # Statistical API job, scene entity
    assert store.entities == before
    assert so.remove_job_index(TENANT, PARCEL, {**single, "scene_id": str(SCENE_UUID),
                                                "scene_product_id": PID_B}, "NDVI")
    assert store.entities == {}
    # Two composites ending the same day: only the stored window's job removes its values.
    take = so.composite_take(date(2026, 6, 1), DAY)
    so.write_optical(TENANT, so.parcel_target(PARCEL, GEOM), take,
                     {**so.index_analytics("NDVI", STATS), "pixelCount": 40}, index_type="NDVI")
    window = {"is_composite": "2026-06-21", "sensing_date": "2026-06-18"}
    so.remove_job_index(TENANT, PARCEL, {**window, "date_range": {"start": "2026-06-10", "end": "2026-06-21"}}, "NDVI")
    assert len(store.entities) == 1
    so.remove_job_index(TENANT, PARCEL, {**window, "date_range": {"start": "2026-06-01", "end": "2026-06-21"}}, "NDVI")
    assert store.entities == {}


def test_retired_zero_index_leaves_both_forms(store, monkeypatch):
    from app.jobs import interior_backfill as ib

    _write_scene(PID_A, "NDVI", 0.6, 0.8)
    _write_scene(PID_A, "EVI", 0.4, 0.8)
    broker = ib.OrionBroker()
    broker.list_products = lambda tenant: [{
        "id": "urn:ngsi-ld:EOProduct:acme:parcel-1:2026-06-21", "type": "EOProduct",
        "hasAgriParcel": {"type": "Relationship", "object": PARCEL},
        "pixelCount": {"type": "Property", "value": 0}, "ndvi": {"type": "Property", "value": 0}}]
    monkeypatch.setattr(ib, "_load_jobs", lambda db, t, parcel=None: [])
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: b"", broker=broker)
    assert report.zeros_retired == 1 and report.errors == 0
    assert "evi" in _analytics(store.entities[PARCEL_ID]) and "ndvi" not in _analytics(store.entities[PARCEL_ID])


# ── broker calls of the new form ───────────────────────────────────────────

def _http_error(code):
    exc = RuntimeError(f"{code} error")
    exc.response = MagicMock(status_code=code)
    return exc


def test_broker_calls_of_the_new_form(caplog):
    from tests.fake_orion import FakeAsyncOrion

    class Failing(FakeAsyncOrion):
        async def upsert_entities_batch(self, entities):
            return {"upserted": 0, "errors": [{"detail": "boom"}], "entity_ids": []}

    entity = {"id": PARCEL_ID, "type": "EOSatelliteImagery"}
    with patch.object(fi, "OrionClient", FakeAsyncOrion):
        assert fi.upsert_eo_satellite_imagery(TENANT, entity) == PARCEL_ID
        assert FakeAsyncOrion.last_instance.entities == [entity]
    with patch.object(fi, "OrionClient", Failing):
        assert fi.upsert_eo_satellite_imagery(TENANT, entity) is None
    orion = MagicMock(spec=["get_entity", "delete_entity"])
    with patch.object(fi, "SyncOrionClient", return_value=orion):
        orion.get_entity.side_effect = _http_error(404)
        assert fi.get_eo_satellite_imagery(TENANT, PARCEL_ID) is None
        orion.get_entity.side_effect = _http_error(503)
        with pytest.raises(RuntimeError):
            fi.get_eo_satellite_imagery(TENANT, PARCEL_ID)
        assert fi.delete_eo_satellite_imagery(TENANT, PARCEL_ID) is True
        orion.delete_entity.assert_called_once_with(PARCEL_ID)
        orion.delete_entity.side_effect = _http_error(503)
        assert fi.delete_eo_satellite_imagery(TENANT, PARCEL_ID) is False


def test_stored_footprint_element_becomes_product_bbox():
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Polygon

    scene = _scene()
    scene.footprint = from_shape(Polygon([(0, 0), (1, 0), (1, 1)]), srid=4326)
    assert so.scene_take(scene).scene.footprint == {
        "type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]}


# ── fix round 1 (23-11) ─────────────────────────────────────────────────────


class _HttpError(Exception):
    def __init__(self, status, message="boom"):
        super().__init__(message)
        self.response = MagicMock(status_code=status)


def test_f3_orion_delete_trusts_the_status_not_the_message():
    orion = MagicMock()
    orion.delete_entity.side_effect = _HttpError(500, "urn:...:404 server error")
    assert fi.orion_delete_entity(orion, "urn:x:404") is False
    orion.delete_entity.side_effect = _HttpError(404, "not found")
    assert fi.orion_delete_entity(orion, "urn:x") is True
    orion.delete_entity.side_effect = RuntimeError("connection to 404.example failed")
    assert fi.orion_delete_entity(orion, "urn:x") is False
    orion.delete_entity.side_effect = None
    assert fi.orion_delete_entity(orion, "urn:x") is True


def test_f2_replace_upserts_first_and_prunes_only_leftovers(store, monkeypatch):
    _write_scene(PID_A, "NDVI", 0.6, folder="s3://bucket/a")
    _write_scene(PID_A, "EVI", 0.4, folder="s3://bucket/a")
    order = []
    real_upsert, real_prune = store.upsert, store.delete_attr
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", lambda t, e: (order.append("upsert"), real_upsert(t, e))[1])
    monkeypatch.setattr(fi, "delete_eo_attribute", lambda t, e, a: (order.append("prune"), real_prune(t, e, a))[1])
    assert _write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE
    assert order[0] == "upsert" and set(order[1:]) == {"prune"}
    assert PARCEL_ID not in store.deleted
    assert sorted(store.pruned) == ["contentInformation", "localServerPath", "storageFormat"]
    assert "evi" not in _analytics(store.entities[PARCEL_ID])


def test_f2_failed_upsert_keeps_the_old_take_and_prunes_nothing(store, caplog, monkeypatch):
    _write_scene(PID_A, "NDVI", 0.6, folder="s3://bucket/a")
    before = copy.deepcopy(store.entities[PARCEL_ID])
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", lambda t, e: None)
    with caplog.at_level(logging.ERROR):
        assert _write_scene(PID_B, "NDVI", 0.7, 0.9) is None
    assert store.entities[PARCEL_ID] == before and not store.pruned and not store.deleted


def test_f2_failed_prune_is_logged_and_the_write_counts(store, caplog, monkeypatch):
    _write_scene(PID_A, "NDVI", 0.6, folder="s3://bucket/a")
    monkeypatch.setattr(fi, "delete_eo_attribute", lambda t, e, a: "error")
    with caplog.at_level(logging.ERROR):
        assert _write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE
    text = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert TENANT in text and PARCEL in text and "s2-indices" in text and "2026-06-21" in text and "localServerPath" in text
    assert store.entities[PARCEL_ID]["productID"]["value"] == PID_B


def test_f4_losing_job_of_a_rasterless_entity_keeps_the_winner(store):
    _write_scene(PID_B, "NDVI", 0.6, 0.8)  # no raster folder: no localServerPath to tell scenes apart
    assert "localServerPath" not in store.entities[PARCEL_ID]
    before = copy.deepcopy(store.entities)
    loser = {"sensing_date": DAY.isoformat(), "scene_id": str(uuid.uuid4()), "scene_product_id": PID_A}
    assert so.remove_job_index(TENANT, PARCEL, loser, "NDVI")
    assert store.entities == before
    winner = {"sensing_date": DAY.isoformat(), "scene_id": str(SCENE_UUID), "scene_product_id": PID_B}
    assert so.remove_job_index(TENANT, PARCEL, winner, "NDVI")
    assert store.entities == {}


def test_f4_statistical_api_job_never_removes_a_scenes_values(store):
    _write_scene(PID_B, "NDVI", 0.6, 0.8)
    before = copy.deepcopy(store.entities)
    assert so.remove_job_index(TENANT, PARCEL, {"sensing_date": DAY.isoformat()}, "NDVI")
    assert store.entities == before


def test_f4_neither_has_a_scene_it_is_the_jobs_take(store):
    take = so.no_scene_take(DAY)
    a = so.index_analytics("NDVI", STATS)
    a["pixelCount"] = 40
    so.write_optical(TENANT, so.parcel_target(PARCEL, GEOM), take, a, index_type="NDVI")
    assert so.remove_job_index(TENANT, PARCEL, {"sensing_date": DAY.isoformat()}, "NDVI")
    assert store.entities == {}


def test_f4_with_scene_product_resolves_the_stac_id():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(scene_id=PID_A)
    out = so.with_scene_product(db, TENANT, {"scene_id": str(SCENE_UUID), "x": 1})
    assert out == {"scene_id": str(SCENE_UUID), "x": 1, "scene_product_id": PID_A}
    db.query.side_effect = RuntimeError("db down")
    assert so.with_scene_product(db, TENANT, {"scene_id": str(SCENE_UUID)}) == {"scene_id": str(SCENE_UUID)}


def test_f7_job_family_day_survives_a_malformed_window():
    for bad in ("2026-06-01", ["x"], 5, None):
        assert so.job_family_day({"is_composite": True, "date_range": bad}) is None
    assert so.job_family_day({"sensing_date": 5}) is None
    assert so.job_family_day({"is_composite": True, "date_range": {"end": "2026-06-21"}}) == (Family.S2_COMPOSITE, DAY)
    # and the delete path that calls it
    assert so.remove_job_index(TENANT, PARCEL, {"is_composite": True, "date_range": "oops"}, "NDVI") is True


# ── F1: zone backfill ───────────────────────────────────────────────────────

ZB_ZONE_EID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:zone:parcel-1:run-1:L1:s2-indices:2026-06-21"


@pytest.fixture
def zone_backfill(store, monkeypatch):
    import app.jobs.zoning_multidate.dates as dates
    import app.services.zone_backfill as zb
    import app.services.zone_products as zp
    from tests.test_zone_backfill import LEFT, PARCEL as ZB_PARCEL

    zone = {"zone_id": "run-1-L1", "zone_urn": ZONE, "zone_version": "v", "geometry": LEFT,
            "relation": zp.CURRENT_ZONE_RELATION, "level": "high", "run_id": "run-1"}
    row = SimpleNamespace(job_id="j1", sensing_date=DAY, raster_path="x.tif", pending=False,
                          created_at=datetime(2026, 6, 22, tzinfo=timezone.utc), scene_id=str(SCENE_UUID))
    state = SimpleNamespace(zones=[zone], rows=[row], legacy=[], zb=zb)
    monkeypatch.setattr(zp, "fetch_current_zones", lambda t, p: state.zones)
    monkeypatch.setattr(dates, "load_raster_rows", lambda db, t, p, i: state.rows)
    monkeypatch.setattr(zb, "_parcel_geometry", lambda t, p: ZB_PARCEL)
    # rasterio is stubbed in this file: the zonal statistics are a fixed value
    monkeypatch.setattr(zp, "zone_statistics", lambda *a, **k: {**STATS, "pixel_count": 40, "valid_fraction": 1.0})
    monkeypatch.setattr(fi, "upsert_eo_zone_index", lambda *a, **k: state.legacy.append(a) or True)
    state.db = MagicMock()
    state.db.query.return_value.filter.return_value.first.return_value = _scene()
    state.run = lambda: zb.backfill_current_zone_products(
        state.db, TENANT, PARCEL, read_raster=lambda r, g: np.full(g.shape, 0.6, dtype="float32"))
    return state


def test_f1_zone_backfill_writes_the_zone_entity_of_the_rows_scene(zone_backfill, store):
    assert zone_backfill.run() == 1 and len(zone_backfill.legacy) == 1
    entity = store.entities[ZB_ZONE_EID]
    assert entity["productID"]["value"] == PID_B and "ndvi" in _analytics(entity)
    assert "localServerPath" not in entity
    assert _analytics(entity)["cloudCoverage"] == "12.50"


def test_f1_zone_backfill_switch_off_writes_only_the_old_form(zone_backfill, store, monkeypatch):
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", "")
    assert zone_backfill.run() == 1 and len(zone_backfill.legacy) == 1
    assert store.entities == {} and store.upserts == []


def test_f1_zone_backfill_skips_old_zones_and_a_day_the_parcel_holds_with_another_scene(
        zone_backfill, store):
    zone_backfill.zones[0]["relation"] = "refAgriManagementZone"
    assert zone_backfill.run() == 1 and ZB_ZONE_EID not in store.entities
    zone_backfill.zones[0]["relation"] = "hasAgriManagementZone"
    _write_scene(PID_A, "NDVI", 0.5, 0.8)  # the parcel's day is scene A's
    assert zone_backfill.run() == 1 and ZB_ZONE_EID not in store.entities


def test_f1_zone_backfill_new_form_failure_changes_nothing(zone_backfill, store, monkeypatch):
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", lambda t, e: None)
    assert zone_backfill.run() == 1 and len(zone_backfill.legacy) == 1
    monkeypatch.setattr(so, "write_optical", MagicMock(side_effect=RuntimeError("boom")))
    assert zone_backfill.run() == 1


def test_f1_zone_backfill_scene_that_does_not_resolve_writes_old_form_only(zone_backfill, store):
    zone_backfill.db.query.return_value.filter.return_value.first.return_value = None
    assert zone_backfill.run() == 1 and ZB_ZONE_EID not in store.entities


# ── F5, F6, F8, F9 ──────────────────────────────────────────────────────────


def test_f5_recompute_without_valid_interior_drops_the_old_interior_group(store):
    from app.jobs import interior_backfill as ib

    _write_scene(PID_B, "NDVI", 0.6, 0.8, extra={"ndvi.interiorMedian": 0.6, "ndvi.interiorP10": 0.5,
                                                  "ndvi.interiorP90": 0.7})
    _write_scene(PID_B, "EVI", 0.4, 0.8)
    before = _analytics(store.entities[PARCEL_ID])
    assert "ndvi.interiorMean" in before and "ndvi.interiorP90" in before
    empty = {"valid_fraction": 0.0, "valid_pixels": 0, "mask": "core",
             "mean": None, "median": None, "p10": None, "p90": None}
    ib.OrionBroker().upsert_satimg(TENANT, PARCEL, "NDVI", statistics=STATS, interior=empty,
                                   raster_path="x/y/ndvi.tif", bucket="bucket", geometry=GEOM, scene=_scene())
    values = _analytics(store.entities[PARCEL_ID])
    assert values["ndvi.interiorValidFraction"] == "0.0000"
    assert not {"ndvi.interiorMean", "ndvi.interiorMedian", "ndvi.interiorP10", "ndvi.interiorP90"} & set(values)
    assert values["evi.interiorMean"] == before["evi.interiorMean"]  # other indices untouched


def _reconcile_row(**kw):
    base = dict(tenant_id=TENANT, entity_id=PARCEL, index_type="NDVI", statistics={"mean": 0.5, "valid_pixels": 9},
                geometry=GEOM, interior=None, parcel_cloud_fraction=None, sensing_date="2026-06-21",
                start_date=None, end_date=None, window_start=None, window_end=None, raster_path=None,
                scene_product_id=None, scene_sensing_date=None, scene_acquisition_datetime=None,
                scene_cloud_coverage=None, scene_footprint=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_f6_job_whose_scene_no_longer_resolves_is_not_written_as_statistical_api(store, monkeypatch, caplog):
    gone = _reconcile_row(job_scene_id=str(uuid.uuid4()))
    with caplog.at_level(logging.WARNING):
        assert _reconcile(monkeypatch, [gone]).main() == 0
    assert store.entities == {}
    assert any(r.levelno == logging.WARNING and "no longer resolves" in r.getMessage() for r in caplog.records)
    # a job that never had a scene still is the Statistical API take
    assert _reconcile(monkeypatch, [_reconcile_row(job_scene_id=None)]).main() == 0
    assert "productID" not in store.entities[PARCEL_ID]


def test_f8_retire_zeros_skips_foreign_ids_and_never_counts_new_form_failures(store, caplog):
    from app.jobs import interior_backfill as ib

    calls = []

    class Broker:
        def delete_attr(self, tenant, eid, key):
            return "deleted"

        def remove_satimg_index(self, tenant, parcel, day, idx):
            calls.append((parcel, day, idx))
            return False  # a failed removal of the new form

    zeros = [
        ("urn:ngsi-ld:EOProduct:acme:parcel-1:not-a-date", "ndvi", PARCEL),      # no date at the end
        ("urn:ngsi-ld:EOProduct:acme:zone:parcel-1:run-1:L1:2026-06-21", "ndvi", PARCEL),  # zone product
        ("urn:ngsi-ld:EOProduct:acme:parcel-1:GRD:2026-06-21", "ndvi", PARCEL),  # GRD product
        ("urn:ngsi-ld:EOProduct:acme:parcel-1:2026-06-21", "ndvi", PARCEL),      # the parcel's optical product
    ]
    report = ib.BackfillReport()
    with caplog.at_level(logging.ERROR):
        ib._retire_zeros(TENANT, Broker(), True, report, zeros, set())
    assert calls == [(PARCEL, DAY, "ndvi")]
    assert report.zeros_retired == 4 and report.errors == 0
    assert any(r.levelno == logging.ERROR and PARCEL in r.getMessage() for r in caplog.records)


def test_f9_reconcile_and_baseline_new_form_never_raise(store, monkeypatch):
    from app.tasks import historical_baseline as hb

    mod = _reconcile(monkeypatch, [_reconcile_row(sensing_date="not-a-date")])
    mod.publish_satimg(_reconcile_row(sensing_date="not-a-date"), {"mean": 0.5})  # must not raise
    monkeypatch.setattr(so, "write_optical", MagicMock(side_effect=RuntimeError("boom")))
    mod.publish_satimg(_reconcile_row(), {"mean": 0.5, "pixel_count": 4})
    hb._write_satimg(TENANT, PARCEL, GEOM, "NDVI", dict(STATS), {"id": PID_A, "cloud_cover": 4}, DAY)
    monkeypatch.setattr(hb, "satimg_enabled", MagicMock(side_effect=RuntimeError("env")))
    hb._write_satimg(TENANT, PARCEL, GEOM, "NDVI", dict(STATS), {"id": PID_A}, DAY)


# ── F10, F11 ────────────────────────────────────────────────────────────────

COMPOSITE_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s2-composite:2026-06-21"


def _write_composite(start, index="NDVI", target=None, kind="parcel"):
    take = so.composite_take(start, DAY)
    a = so.index_analytics(index, STATS)
    a["pixelCount"] = 40
    tgt = target or (so.parcel_target(PARCEL, GEOM) if kind == "parcel" else so.zone_target(PARCEL, ZONE, GEOM))
    return so.write_optical(TENANT, tgt, take, a, index_type=index)


def test_f10_composite_carries_its_window_and_the_last_computed_window_wins(store):
    assert _write_composite(date(2026, 6, 1)) == so.CREATE
    text = store.entities[COMPOSITE_ID]["resultDescription"]["value"]
    assert "2026-06-01" in text and "2026-06-21" in text  # start and end
    assert _write_composite(date(2026, 6, 10), "EVI") == so.REPLACE  # other window, same end day
    entity = store.entities[COMPOSITE_ID]
    text = entity["resultDescription"]["value"]
    assert "2026-06-10" in text and "2026-06-01" not in text and "2026-06-21" in text
    assert set(_analytics(entity)) >= {"evi"} and "ndvi" not in _analytics(entity)
    assert _write_composite(date(2026, 6, 10), "SAVI") == so.MERGE  # same window merges


def test_f11_dry_run_reconcile_writes_nothing(store, monkeypatch, capsys):
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)
    legacy = MagicMock(return_value="x")
    mod = _reconcile(monkeypatch, [_reconcile_row(scene_product_id=PID_A, scene_sensing_date=DAY)])
    monkeypatch.setattr(mod, "upsert_eo_index", legacy)
    monkeypatch.setattr(mod, "publish_satimg", MagicMock())
    monkeypatch.setattr(sys, "argv", ["x", "--tenants", TENANT, "--dry-run"])
    assert mod.main() == 0
    assert "would publish" in capsys.readouterr().out
    legacy.assert_not_called()
    mod.publish_satimg.assert_not_called()
    assert store.entities == {} and store.upserts == [] and store.deleted == []


def test_f11_delete_removes_zone_and_parcel_composites(store):
    from app.services import zone_products as zp

    zone = {"zone_id": "run-1-L1", "zone_urn": ZONE, "relation": zp.CURRENT_ZONE_RELATION}
    zone_eid = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:zone:parcel-1:run-1:L1:s2-composite:2026-06-21"
    result = {"is_composite": True, "sensing_date": "2026-06-18",
              "date_range": {"start": "2026-06-01", "end": "2026-06-21"}}
    other = {**result, "date_range": {"start": "2026-06-10", "end": "2026-06-21"}}
    _write_composite(date(2026, 6, 1))
    _write_composite(date(2026, 6, 1), kind="zone")
    assert {COMPOSITE_ID, zone_eid} <= set(store.entities)
    with patch.object(zp, "fetch_zone_targets", return_value=[zone]), patch.object(fi, "delete_eo_index"):
        zp.delete_zone_products(TENANT, PARCEL, "2026-06-18", "NDVI", job_result=other)  # another window
        assert so.remove_job_index(TENANT, PARCEL, other, "NDVI")
        assert {COMPOSITE_ID, zone_eid} <= set(store.entities)
        zp.delete_zone_products(TENANT, PARCEL, "2026-06-18", "NDVI", job_result=result)
        assert zone_eid not in store.entities and COMPOSITE_ID in store.entities
        assert so.remove_job_index(TENANT, PARCEL, result, "NDVI")
    assert COMPOSITE_ID not in store.entities


# ── fix round 2: prune allowlist ────────────────────────────────────────────


def _prune(raw, entity):
    calls = []
    with patch.object(fi, "delete_eo_attribute", side_effect=lambda t, e, a: calls.append(a) or "deleted"):
        so._prune_leftovers(TENANT, PARCEL_ID, raw, entity, "ctx")
    return calls


def test_r2_prune_never_deletes_what_the_new_entity_carries_under_another_name():
    prop = {"type": "Property", "value": 1}
    raw = {"id": PARCEL_ID, "type": "EOSatelliteImagery", "refAgriParcel": prop,
           "nkz:hasAgriManagementZone": prop,
           "https://uri.etsi.org/ngsi-ld/default-context/hasAgriManagementZone": prop,
           "https://smartdatamodels.org/dataModel.SatelliteImagery/productID": prop}
    entity = {"id": PARCEL_ID, "type": "EOSatelliteImagery", "hasAgriParcel": prop, "hasAgriManagementZone": prop,
              "productID": prop}
    assert _prune(raw, entity) == []


def test_r2_replace_without_raster_prunes_exactly_the_raster_attributes_by_short_name():
    prop = {"type": "Property", "value": 1}
    raw = {"id": PARCEL_ID, "type": "EOSatelliteImagery", "productID": prop, "platformName": prop,
           "https://smartdatamodels.org/dataModel.SatelliteImagery/localServerPath": prop,
           "storageFormat": prop, "contentInformation": prop, "hasAgriParcel": prop, "analytics": prop}
    entity = {"id": PARCEL_ID, "type": "EOSatelliteImagery", "productID": prop, "platformName": prop,
              "hasAgriParcel": prop, "analytics": prop}
    assert sorted(_prune(raw, entity)) == ["contentInformation", "localServerPath", "storageFormat"]


def test_r2_unknown_and_protected_stored_attributes_are_left_alone():
    prop = {"type": "Property", "value": 1}
    raw = {"id": PARCEL_ID, "type": "EOSatelliteImagery", "somethingElse": prop, "hasAgriParcel": prop,
           "analysisType": prop, "provider": prop, "sensingDate": prop, "resultDescription": prop,
           "analytics": prop, "productType": prop, "location": prop}
    assert _prune(raw, {"id": PARCEL_ID, "type": "EOSatelliteImagery"}) == []


def test_r2_prune_404_is_not_an_error_other_failures_are(caplog):
    prop = {"type": "Property", "value": 1}
    raw = {"id": PARCEL_ID, "productID": prop}
    entity = {"id": PARCEL_ID}
    http = MagicMock(status_code=404)
    err = RuntimeError("404 gone")
    err.response = http
    with caplog.at_level(logging.WARNING):
        with patch.object(fi, "SyncOrionClient") as client:
            client.return_value.delete_entity_attr.side_effect = err
            so._prune_leftovers(TENANT, PARCEL_ID, raw, entity, "ctx")
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        err.response = MagicMock(status_code=500)
        with patch.object(fi, "SyncOrionClient") as client:
            client.return_value.delete_entity_attr.side_effect = err
            so._prune_leftovers(TENANT, PARCEL_ID, raw, entity, "ctx")
    assert [r for r in caplog.records if r.levelno >= logging.ERROR and "productID" in r.getMessage()]


def test_r2_with_scene_product_without_scene_id_neither_queries_nor_logs(caplog):
    db = MagicMock()
    with caplog.at_level(logging.WARNING):
        out = so.with_scene_product(db, TENANT, {"is_composite": True, "date_range": {"start": "a", "end": "b"}})
        assert so.with_scene_product(db, TENANT, None) == {}
    assert not db.query.called and not caplog.records and out["is_composite"] is True


def test_r2_unresolved_scene_never_removes_a_winner_on_a_guess(store, caplog):
    _write_scene(PID_B, "NDVI", 0.6, 0.8, folder=f"s3://bucket/{TENANT}/entities/{PARCEL}/scenes/{SCENE_UUID}")
    before = copy.deepcopy(store.entities)
    job = {"sensing_date": DAY.isoformat(), "scene_id": str(SCENE_UUID)}  # no scene_product_id: lookup failed
    with caplog.at_level(logging.WARNING):
        assert so.remove_job_index(TENANT, PARCEL, job, "NDVI")
    assert store.entities == before
    assert any(r.levelno == logging.WARNING and "unresolved" in r.getMessage() for r in caplog.records)


def test_r2_zone_backfill_reads_the_parcel_once_per_row(zone_backfill, store, monkeypatch):
    from tests.test_zone_backfill import RIGHT

    zone_backfill.zones.append({**zone_backfill.zones[0], "zone_id": "run-1-L2", "geometry": RIGHT,
                                "zone_urn": ZONE.replace("L1", "L2")})
    real = fi.get_eo_satellite_imagery
    parcel_reads = []
    monkeypatch.setattr(fi, "get_eo_satellite_imagery",
                        lambda t, e: (parcel_reads.append(e) if ":parcel:" in e else None, real(t, e))[1])
    assert zone_backfill.run() == 2
    assert len(parcel_reads) == 1
