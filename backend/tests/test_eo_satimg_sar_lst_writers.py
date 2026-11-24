"""Radar and surface temperature writers: EOSatelliteImagery next to the legacy EOProduct, per-tenant switch.

The broker is a dict with ``options=update`` semantics (attributes sent overwrite, attributes not sent
stay), so a "replace" that forgets the leftovers fails here.
"""
import asyncio
import copy
import json
import logging
import os
import sys
import uuid
from contextlib import ExitStack
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows", "rasterio.control",
    "rasterio.transform", "rasterio.mask", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import pytest
from nkz_platform_sdk import parse_eo_entities

from app.services import fiware_integration as fi

TENANT = "acme"
PARCEL = "urn:ngsi-ld:AgriParcel:parcel-1"
ZONE = "urn:ngsi-ld:AgriManagementZone:parcel-1:run-1:L1"
TERRAIN_ZONE = "urn:ngsi-ld:AgriParcelZone:parcel-1:t1"
DAY = date(2026, 6, 21)
S1_A = "S1A_IW_GRDH_1SDV_20260621T054321_20260621T054346_059700_0758AB_1A2B"
S1_B = "S1C_IW_GRDH_1SDV_20260621T172001_20260621T172026_004100_00A1B2_3C4D"
GEOM = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]}
FOOT = {"type": "Polygon", "coordinates": [[[-1.0, -1.0], [2.0, -1.0], [2.0, 2.0], [-1.0, -1.0]]]}
LANDSAT_ID = "LC09_L2SP_200031_20260620_20260621_02_T1_ST"
CLMS_ID = "c_gls_LST-20260620T1100_GLOBE_MSG_V3.0.1"

SCENE_A = {
    "id": S1_A, "sensing_date": "2026-06-21", "datetime": "2026-06-21T05:43:21.123456Z",
    "platform": "sentinel-1a", "relative_orbit": 103, "orbit_state": "descending",
    "geometry": FOOT, "instrument_mode": "IW", "product_type": "IW_GRDH_1S",
}
VV = {"mean": -12.0, "min": -20.0, "max": -5.0, "pixel_count": 80, "valid_fraction": 0.9}
VH = {"mean": -18.5, "min": -25.0, "max": -10.0, "pixel_count": 70, "valid_fraction": 0.8}
SMI = {"mean": 0.3, "min": 0.1, "max": 0.6, "std": 0.05, "pixel_count": 75}
ZONE_STATS = {"mean": -9.0, "min": -15.0, "max": -4.0, "pixel_count": 30, "valid_fraction": 0.95}

BS_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s1-backscatter:2026-06-21"
SM_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s1-soil-moisture:2026-06-21"
CH_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:s1-change:2026-06-21"
ZN_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:zone:parcel-1:run-1:L1:s1-backscatter:2026-06-21"
LS_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:landsat-lst:2026-06-20"
CL_ID = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:parcel-1:clms-lst:2026-06-20"


class Store:
    """Broker stand-in: one dict per entity, upsert = options=update."""

    def __init__(self):
        self.entities = {}
        self.upserts = []
        self.deleted = []
        self.pruned = []
        self.fail_upsert = False
        self.fail_read = False

    def get(self, tenant, eid):
        if self.fail_read:
            raise RuntimeError("broker read failed")
        e = self.entities.get(eid)
        return copy.deepcopy(e) if e is not None else None

    def upsert(self, tenant, entity):
        if self.fail_upsert:
            return None
        self.upserts.append(copy.deepcopy(entity))
        self.entities.setdefault(entity["id"], {}).update(copy.deepcopy(entity))
        return entity["id"]

    def delete(self, tenant, eid):
        self.deleted.append(eid)
        self.entities.pop(eid, None)
        return True

    def delete_attr(self, tenant, eid, attr):
        self.pruned.append((eid, attr))
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
    return s


@pytest.fixture
def rl():
    from app.services import eo_satimg_radar_lst
    return eo_satimg_radar_lst


def _analytics(entity):
    return {i["name"]: i["value"] for i in entity["analytics"]["value"]}


def _prop(entity, name):
    return entity[name]["value"]


def _assert_parse(store, expected):
    """Every stored entity parses to exactly one observation with its family and day."""
    seen = {}
    for eid, entity in getattr(store, "entities", store).items():
        found = parse_eo_entities([entity])
        assert len(found) == 1, eid
        seen[eid] = (found[0].family, found[0].sensing_day)
    assert seen == expected


# ── the three dispatchers ───────────────────────────────────────────────────

PARAM_KEYS = {"scene_datetime", "scene_platform", "scene_relative_orbit", "orbit_direction",
              "scene_footprint", "instrument_mode"}


def _expected_params(params):
    assert PARAM_KEYS <= set(params), PARAM_KEYS - set(params)
    assert params["scene_datetime"] == SCENE_A["datetime"]
    assert params["scene_platform"] == "sentinel-1a"
    assert params["scene_relative_orbit"] == 103
    assert params["orbit_direction"] == "DESCENDING"
    assert params["scene_footprint"] == FOOT
    assert params["instrument_mode"] == "IW"
    assert params["scene_product_type"] == "IW_GRDH_1S"
    json.dumps(params)  # the parameters go into a JSON column
    assert params["sensing_date"] == "2026-06-21"


def test_dispatchers_pass_scene_metadata(rl):
    # local_analyze
    from app.services import local_analyze

    copernicus = MagicMock()
    copernicus.search_s1_scenes.return_value = [SCENE_A]
    db = MagicMock()
    with patch("app.tasks.sar_tasks.download_sentinel1_scene") as task:
        local_analyze._dispatch_sar(
            db, copernicus=copernicus, tenant_id=TENANT, entity_id=PARCEL, intersects_geojson=GEOM,
            bbox=[0, 0, 1, 1], start_date_iso="2026-06-01", end_date_iso="2026-06-30",
            crop_season_id=None, season_uuid=None,
        )
    _expected_params(task.delay.call_args.kwargs["parameters"])

    # api/sar.py
    from app.api import sar as sar_api

    request = sar_api.SarAnalyzeRequest(entity_id=PARCEL, start_date="2026-06-01", end_date="2026-06-30")
    client = MagicMock()
    client.search_s1_scenes.return_value = [SCENE_A]
    api_db = MagicMock()
    with patch.object(sar_api, "SyncOrionClient"), \
            patch("app.services.fiware_integration.orion_get_entity",
                  return_value={"location": {"value": GEOM}}), \
            patch.object(sar_api, "CopernicusDataSpaceClient", return_value=client), \
            patch.object(sar_api, "get_copernicus_credentials_with_fallback", return_value=None), \
            patch.object(sar_api, "download_sentinel1_scene") as api_task:
        asyncio.run(sar_api.analyze_sar(request, current_user={"tenant_id": TENANT}, db=api_db))
    _expected_params(api_task.delay.call_args.kwargs["parameters"])

    # scheduler
    import app.tasks.scheduler as sched

    sub = MagicMock()
    sub.id = uuid.uuid4()
    sub.tenant_id = TENANT
    sub.entity_id = PARCEL
    sub.status = "active"
    sub.index_types = ["NDVI"]
    sub.geometry = object()
    sub.start_date = date(2026, 4, 1)
    sub.frequency = "daily"
    sub.last_run_at = None
    sub.next_run_at = None
    sub.last_error = None
    sched_client = MagicMock()
    sched_client.search_scenes.return_value = [
        {"id": "S2A_X", "sensing_date": "2026-06-20", "datetime": "2026-06-20T10:50:00Z", "cloud_cover": 5}]
    sched_client.search_s1_scenes.return_value = [SCENE_A]
    sched_db = MagicMock()

    def query(*args):
        q = MagicMock()
        q.filter.return_value = q
        if args and args[0] is sched.VegetationSubscription:
            q.first.return_value = sub
        else:
            q.all.return_value = []
        return q

    sched_db.query.side_effect = query
    shape = MagicMock()
    shape.bounds = (0.0, 0.0, 1.0, 1.0)
    shape.geom_type = "Polygon"
    shape.__geo_interface__ = GEOM
    with patch.object(sched, "get_db_session", return_value=iter([sched_db])), \
            patch("geoalchemy2.shape.to_shape", return_value=shape), \
            patch("app.services.copernicus_client.CopernicusDataSpaceClient", return_value=sched_client), \
            patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
                  return_value={"client_id": "i", "client_secret": "s"}), \
            patch.object(sched.acquisition_guard, "disk_ok", return_value=(True, 100.0, 20.0)), \
            patch.object(sched.acquisition_guard, "tenant_budget", return_value=20), \
            patch.object(sched.parcel_scenes, "scenes_seen", return_value=set()), \
            patch.object(sched.parcel_scenes, "record"), \
            patch.object(sched, "download_sentinel2_scene"), \
            patch("app.tasks.sar_tasks.download_sentinel1_scene") as sched_task:
        sched.check_and_process_entity.run(str(sub.id), "2026-04-01", "2026-06-30")
    _expected_params(sched_task.delay.call_args.kwargs["parameters"])


def test_scene_job_params_have_one_definition(rl):
    params = rl.scene_job_params(SCENE_A)
    _expected_params({**params, "sensing_date": "2026-06-21"})
    # nothing is invented for a scene the catalogue says little about
    assert rl.scene_job_params({"id": "x", "sensing_date": "2026-06-21"}) == {}


def test_search_s1_scenes_returns_platform_and_orbit():
    from app.services.copernicus_client import CopernicusDataSpaceClient

    feature = {"id": S1_A, "geometry": FOOT, "assets": {}, "links": [], "properties": {
        "datetime": "2026-06-21T05:43:21.123456Z", "platform": "sentinel-1a", "sat:relative_orbit": 103,
        "sat:orbit_state": "descending", "sar:instrument_mode": "IW", "sar:polarizations": ["VV", "VH"],
        "product:type": "IW_GRDH_1S"}}
    client = CopernicusDataSpaceClient("id", "secret")
    response = MagicMock()
    response.json.return_value = {"features": [feature]}
    with patch.object(client, "_get_optional_auth_headers", return_value={}), \
            patch("app.services.copernicus_client.requests.post", return_value=response):
        scene = client.search_s1_scenes(bbox=[0, 0, 1, 1], start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))[0]
    assert (scene["platform"], scene["relative_orbit"], scene["orbit_state"]) == ("sentinel-1a", 103, "descending")
    assert scene["instrument_mode"] == "IW" and scene["geometry"] == FOOT
    assert scene["product_type"] == "IW_GRDH_1S"


# ── download_sentinel1_scene harness ────────────────────────────────────────

GRD_PARAMS = {
    "scene_id": S1_A, "entity_id": PARCEL, "sensing_date": "2026-06-21", "bounds": GEOM,
    "scene_datetime": SCENE_A["datetime"], "scene_platform": "sentinel-1a", "scene_relative_orbit": 103,
    "orbit_direction": "DESCENDING", "scene_footprint": FOOT, "instrument_mode": "IW",
    "scene_product_type": "IW_GRDH_1S",
}


def run_s1(params=None, *, vv=VV, vh=VH, smi=SMI, previous_vv=-5.0, zones=(), summary=None,
           scene_id=None, client=None, events=None):
    """Run the S1 task with the rasters, SMI, zones and previous take replaced; return what it did."""
    from app.tasks.sar_tasks import download_sentinel1_scene

    params = dict(GRD_PARAMS if params is None else params)
    if scene_id:
        params["scene_id"] = scene_id
    old = []
    if client is None:
        client = MagicMock()
        paths = {k: f"/tmp/{k}.tif" for k, st in (("vv", vv), ("vh", vh)) if st is not None}
        client.download_s1_bands.return_value = paths
        client.download_s1_calibration.return_value = {k: f"/tmp/{k}.xml" for k in paths}
        client.get_s1_scene_summary.return_value = summary

    def calibrated(band, calibration, geom, workdir):
        return dict(vv if "vv" in band else vh), "/tmp/db_parcel.tif"

    job = MagicMock()
    job.id = uuid.uuid4()
    session = MagicMock()
    session.query.return_value.filter.return_value.first.side_effect = [job, None]

    def recorder(tenant, entity):
        old.append((tenant, copy.deepcopy(entity)))
        if events is not None:
            events.append(("old", entity["id"]))
        return {"upserted": 1}

    prev = [] if previous_vv is None else [{"sensingDate": "2026-06-09", "backscatterVV": previous_vv}]

    def query_entities(**kw):
        return prev if 'productType=="GRD"' in kw.get("q", "") else []

    orion = MagicMock()
    orion.query_entities.side_effect = query_entities
    smi_mock = MagicMock(return_value=dict(smi) if smi else {"pixel_count": 0})
    notify = MagicMock(return_value=True)

    with ExitStack() as stack:
        enter = stack.enter_context
        enter(patch("app.tasks.sar_tasks.get_db_session", return_value=iter([session])))
        enter(patch("app.tasks.sar_tasks.get_copernicus_credentials_with_fallback",
                    return_value={"client_id": "i", "client_secret": "s"}))
        enter(patch("app.tasks.sar_tasks.CopernicusDataSpaceClient", return_value=client))
        enter(patch("app.tasks.sar_tasks._calibrated_parcel", side_effect=calibrated))
        enter(patch("app.tasks.sar_tasks._upload_sar_cog", return_value="k/x.tif"))
        enter(patch("app.tasks.sar_tasks._zone_sigma0_stats", return_value=dict(ZONE_STATS)))
        enter(patch("app.tasks.sar_tasks._notify_field_operations", notify))
        enter(patch("app.services.zone_products.fetch_zone_targets", return_value=list(zones)))
        enter(patch("app.services.sar_smi_processor.compute_smi", smi_mock))
        enter(patch("nkz_platform_sdk.SyncOrionClient", return_value=orion))
        enter(patch("os.path.isfile", return_value=True))
        enter(patch.object(fi, "_upsert_eoproduct_entity", side_effect=recorder))
        enter(patch.object(download_sentinel1_scene, "update_state"))
        download_sentinel1_scene(job_id=str(uuid.uuid4()), tenant_id=TENANT, parameters=params)
    return SimpleNamespace(old=old, job=job, smi=smi_mock, client=client, notify=notify)


CURRENT_ZONE = {"zone_id": "run1abcd-L1", "zone_urn": ZONE, "geometry": GEOM,
                "relation": "hasAgriManagementZone", "zone_version": "run-1", "level": "L1", "run_id": "run-1"}
TERRAIN = {"zone_id": "t1", "zone_urn": TERRAIN_ZONE, "geometry": GEOM, "relation": "hasAgriParcelZone"}


def test_backscatter_entity_has_sensing_date_and_scene(store, rl):
    run_s1()
    entity = store.entities[BS_ID]
    assert _prop(entity, "analysisType") == "sar-backscatter"
    assert _prop(entity, "sensingDate") == "2026-06-21T05:43:21Z"
    assert _prop(entity, "productID") == S1_A
    assert _prop(entity, "productURL").endswith(f"/collections/sentinel-1-grd/items/{S1_A}")
    assert _prop(entity, "productType") == "IW_GRDH_1S"  # the item's own code
    assert _prop(entity, "platformName") == "S1A"
    assert _prop(entity, "orbitNumber") == 103
    assert _prop(entity, "operationalMode") == "IW"
    assert entity["productBbox"]["value"] == FOOT
    assert entity["processedBbox"]["value"] == GEOM
    assert entity["hasAgriParcel"]["object"] == PARCEL
    # the stored COG names do not follow {name}.tif: the raster is not declared
    for absent in ("localServerPath", "contentInformation", "storageFormat", "productFormat"):
        assert absent not in entity
    assert _analytics(entity) == {
        "backscatterVV": "-12.0000", "backscatterVH": "-18.5000",
        "pixelCount": "70", "validPixelFraction": "0.8000",  # the smaller of the two polarisations
    }
    assert "incidenceAngle" not in _analytics(entity)


def test_soil_moisture_separate_entity_with_four_analytics(store, rl):
    run_s1()
    entity = store.entities[SM_ID]
    assert _prop(entity, "analysisType") == "sar-soil-moisture"
    assert _prop(entity, "sensingDate") == "2026-06-21T05:43:21Z"
    assert _prop(entity, "productID") == S1_A
    _assert_parse({SM_ID: entity}, {SM_ID: ("s1-soil-moisture", DAY)})
    assert _analytics(entity) == {
        "soilMoistureIndex": "0.3000", "soilMoistureIndex.min": "0.1000",
        "soilMoistureIndex.max": "0.6000", "soilMoistureIndex.std": "0.0500", "pixelCount": "75",
    }
    # the radar values never ride on the soil moisture entity
    assert "backscatterVV" not in _analytics(entity)
    assert not any(n.startswith("backscatter") for n in _analytics(entity))


def test_change_flag_separate_entity_three_analytics(store, rl):
    result = run_s1()
    entity = store.entities[CH_ID]
    assert _prop(entity, "analysisType") == "sar-change-detection"
    assert _prop(entity, "productID") == S1_A
    assert _analytics(entity) == {"changeFlag": "tillage", "changeConfidence": "0.7500", "deltaVV": "-7.0000"}
    assert result.notify.call_count == 1
    _assert_parse(store, {
        BS_ID: ("s1-backscatter", DAY), SM_ID: ("s1-soil-moisture", DAY), CH_ID: ("s1-change", DAY)})


def test_smi_uses_real_orbit_direction(store, rl, caplog):
    result = run_s1()
    assert result.smi.call_args.kwargs["orbit_direction"] == "DESCENDING"

    # a job queued before the change lacks the keys: the catalogue item says it
    old_params = {k: v for k, v in GRD_PARAMS.items() if k not in PARAM_KEYS}
    summary = {"orbit_state": "descending", "datetime": SCENE_A["datetime"], "relative_orbit": 103,
               "platform": "sentinel-1a", "geometry": FOOT, "instrument_mode": "IW"}
    result = run_s1(old_params, summary=summary)
    assert result.smi.call_args.kwargs["orbit_direction"] == "DESCENDING"

    # nothing known: today's default, and it is said
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = run_s1(old_params, summary=None)
    assert result.smi.call_args.kwargs["orbit_direction"] == "ASCENDING"
    assert any("orbit" in r.getMessage().lower() and r.levelno == logging.WARNING for r in caplog.records)


def test_landsat_and_clms_lst_do_not_overwrite(store, rl):
    from app.tasks import lst_tasks

    landsat = {"id": LANDSAT_ID, "geometry": FOOT,
               "properties": {"datetime": "2026-06-20T10:50:12.345678Z", "platform": "landsat-9"}}
    clms = {"id": CLMS_ID, "geometry": FOOT,
            "properties": {"datetime": "2026-06-20T11:00:00Z", "product:type": "land_surface_temperature"}}
    stats = {"mean": 31.456, "min": 28.0, "max": 34.0, "std": 1.5, "pixel_count": 12}
    old = []

    def recorder(tenant, entity):
        old.append(copy.deepcopy(entity))
        return {"upserted": 1}

    with patch.object(fi, "_upsert_eoproduct_entity", side_effect=recorder), \
            patch.object(lst_tasks, "_auth_headers", return_value={}), \
            patch.object(lst_tasks, "process_parcel_lst_item",
                         return_value=(dict(stats), "2026-06-20", LANDSAT_ID, landsat)), \
            patch.object(lst_tasks, "process_clms_lst_item",
                         return_value=(dict(stats), "2026-06-20T11:00:00Z", CLMS_ID, clms)):
        lst_tasks.process_parcel_lst_task.run(TENANT, PARCEL, 0.5, 0.5, GEOM)
        lst_tasks.process_parcel_clms_lst_task.run(TENANT, PARCEL, 0.5, 0.5, GEOM)

    ls, cl = store.entities[LS_ID], store.entities[CL_ID]
    assert _prop(ls, "analysisType") == _prop(cl, "analysisType") == "land-surface-temperature"
    assert _prop(ls, "productID") == LANDSAT_ID and _prop(cl, "productID") == CLMS_ID  # no "CLMS:" prefix
    assert "productType" not in ls  # the item gave none: omitted, no id-token fallback
    assert _prop(cl, "productType") == "land_surface_temperature"
    assert _prop(ls, "platformName") == "LANDSAT_9" and "orbitNumber" not in ls
    for absent in ("platformName", "orbitNumber", "operationalMode"):
        assert absent not in cl
    assert _prop(ls, "sensingDate") == "2026-06-20T10:50:12Z" and _prop(cl, "sensingDate") == "2026-06-20T11:00:00Z"
    assert _analytics(ls) == {"lst": "31.46", "lst.min": "28.00", "lst.max": "34.00", "lst.std": "1.50",
                              "pixelCount": "12"}
    assert _analytics(cl) == _analytics(ls)
    assert len(old) == 2 and {e["id"] for e in old} == {old[0]["id"]}  # the old form still shares one entity
    assert old[1]["lstSourceScene"]["value"] == f"CLMS:{CLMS_ID}"  # old form byte-for-byte
    _assert_parse(store, {LS_ID: ("landsat-lst", date(2026, 6, 20)), CL_ID: ("clms-lst", date(2026, 6, 20))})


def test_single_polarization_still_two_analytics_or_skipped(store, rl, caplog):
    run_s1(vh=None, smi=None, previous_vv=None)
    assert _analytics(store.entities[BS_ID]) == {
        "backscatterVV": "-12.0000", "pixelCount": "80", "validPixelFraction": "0.9000"}
    _assert_parse(store, {BS_ID: ("s1-backscatter", DAY)})

    # a polarisation without a pixel count would leave one analytic: nothing is written
    store.entities.clear()
    store.upserts.clear()
    bare = {"mean": -12.0, "pixel_count": None}
    take = rl.radar_take(S1_A, GRD_PARAMS)
    with caplog.at_level(logging.WARNING):
        verdict = rl.write_backscatter(TENANT, rl.parcel_target(PARCEL, GEOM), take, {"VV": bare})
    assert verdict == rl.SKIP and not store.upserts
    assert any("not written" in r.getMessage() for r in caplog.records)


def test_same_day_larger_coverage_wins(store, rl):
    target = rl.parcel_target(PARCEL, GEOM)
    first = rl.radar_take(S1_A, GRD_PARAMS)
    second = rl.radar_take(S1_B, {**GRD_PARAMS, "scene_id": S1_B, "scene_datetime": "2026-06-21T17:20:01Z",
                                  "scene_relative_orbit": 8, "orbit_direction": "ASCENDING"})

    def vv(pixels):
        return {"VV": {"mean": -10.0, "pixel_count": pixels, "valid_fraction": 0.9}}

    assert rl.write_backscatter(TENANT, target, first, vv(40)) == rl.CREATE
    # smaller coverage loses; so does a tie (the stored take stays)
    assert rl.write_backscatter(TENANT, target, second, vv(30)) == rl.SKIP
    assert rl.write_backscatter(TENANT, target, second, vv(40)) == rl.SKIP
    assert _prop(store.entities[BS_ID], "productID") == S1_A
    # larger coverage replaces the whole take: values, product attributes and the sensing time
    assert rl.write_backscatter(TENANT, target, second, {
        "VV": {"mean": -9.0, "pixel_count": 60, "valid_fraction": 0.95},
        "VH": {"mean": -17.0, "pixel_count": 61, "valid_fraction": 0.96}}) == rl.REPLACE
    entity = store.entities[BS_ID]
    assert _prop(entity, "productID") == S1_B and _prop(entity, "platformName") == "S1C"
    assert _prop(entity, "orbitNumber") == 8 and _prop(entity, "sensingDate") == "2026-06-21T17:20:01Z"
    assert _analytics(entity) == {"backscatterVV": "-9.0000", "backscatterVH": "-17.0000",
                                  "pixelCount": "60", "validPixelFraction": "0.9500"}
    # the same take again completes itself without losing what it had
    assert rl.write_backscatter(TENANT, target, second, {"VV": {"mean": -9.5, "pixel_count": 60,
                                                                 "valid_fraction": 0.95}}) == rl.MERGE
    assert _analytics(store.entities[BS_ID])["backscatterVH"] == "-17.0000"
    assert _analytics(store.entities[BS_ID])["backscatterVV"] == "-9.5000"
    _assert_parse(store, {BS_ID: ("s1-backscatter", DAY)})


def test_replace_prunes_leftover_attributes_of_the_losing_take(store, rl):
    target = rl.parcel_target(PARCEL, GEOM)
    first = rl.radar_take(S1_A, GRD_PARAMS)
    bare = rl.radar_take(S1_B, {"scene_id": S1_B, "sensing_date": "2026-06-21"})  # no orbit, no footprint
    assert rl.write_backscatter(TENANT, target, first, {"VV": {"mean": -10.0, "pixel_count": 10,
                                                                "valid_fraction": 0.9}}) == rl.CREATE
    assert rl.write_backscatter(TENANT, target, bare, {"VV": {"mean": -9.0, "pixel_count": 20,
                                                               "valid_fraction": 0.95}}) == rl.REPLACE
    entity = store.entities[BS_ID]
    assert _prop(entity, "productID") == S1_B
    for gone in ("orbitNumber", "productBbox"):
        assert gone not in entity, gone
    assert _prop(entity, "operationalMode") == "IW"  # read from the scene id, not left over


# ── switch, failures, dates, zones, change ──────────────────────────────────

def _old_entities(result):
    return [e for _, e in result.old]


def test_disabled_tenant_writes_only_old_form(store, monkeypatch, rl):
    enabled = run_s1(zones=[CURRENT_ZONE])
    assert store.upserts
    store.upserts.clear()
    store.entities.clear()
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", "")
    disabled = run_s1(zones=[CURRENT_ZONE])
    assert not store.upserts and not store.entities
    assert _old_entities(disabled) == _old_entities(enabled)  # the old form is byte-identical
    assert len(_old_entities(disabled)) >= 4  # GRD product, zone product, SMI and the change flag


def test_new_form_failure_leaves_job_and_old_form_intact(store, rl, caplog):
    reference = run_s1(zones=[CURRENT_ZONE])
    store.upserts.clear()
    store.entities.clear()
    for failure in ("upsert", "read"):
        store.fail_upsert = failure == "upsert"
        store.fail_read = failure == "read"
        with caplog.at_level(logging.ERROR):
            result = run_s1(zones=[CURRENT_ZONE])
        assert _old_entities(result) == _old_entities(reference), failure
        result.job.mark_completed.assert_called_once()
        result.job.mark_failed.assert_not_called()
        assert any(TENANT in r.getMessage() and "eo_satimg" in r.getMessage() for r in caplog.records), failure


def test_no_sensing_day_skips_the_new_form(store, rl, caplog):
    params = {k: v for k, v in GRD_PARAMS.items() if k not in ("sensing_date", "scene_datetime")}
    params["sensing_date"] = ""
    with caplog.at_level(logging.WARNING):
        result = run_s1(params)
    assert not store.upserts and not store.entities
    assert _old_entities(result)  # the old form keeps its own date fallback
    assert any("sensing day" in r.getMessage() for r in caplog.records)
    assert rl.radar_take(S1_A, {"scene_id": S1_A}) is None


def test_change_follows_backscatter(store, rl):
    # another take already holds the day with more coverage: backscatter loses, so does the change
    rl.write_backscatter(TENANT, rl.parcel_target(PARCEL, GEOM), rl.radar_take(S1_B, {
        "scene_id": S1_B, "sensing_date": "2026-06-21", "scene_datetime": "2026-06-21T17:20:01Z"}),
        {"VV": {"mean": -8.0, "pixel_count": 500, "valid_fraction": 0.99}})
    before = copy.deepcopy(store.entities)
    run_s1()
    assert store.entities[BS_ID] == before[BS_ID]
    assert CH_ID not in store.entities

    # a winning take replaces a change entity left by the other take
    store.entities.clear()
    store.entities[CH_ID] = {
        "id": CH_ID, "type": "EOSatelliteImagery",
        "analysisType": {"type": "Property", "value": "sar-change-detection"},
        "sensingDate": {"type": "Property", "value": "2026-06-21T17:20:01Z"},
        "productID": {"type": "Property", "value": S1_B},
        "orbitNumber": {"type": "Property", "value": 8},
        "hasAgriParcel": {"type": "Relationship", "object": PARCEL},
        "analytics": {"type": "Property", "value": [
            {"name": "changeFlag", "value": "harvest"}, {"name": "deltaVV", "value": "-9.0000"}]},
    }
    run_s1()
    change = store.entities[CH_ID]
    assert _prop(change, "productID") == S1_A and _prop(change, "orbitNumber") == 103
    assert _analytics(change)["changeFlag"] == "tillage"


def test_terrain_zone_skipped(store, rl):
    result = run_s1(zones=[CURRENT_ZONE, TERRAIN])
    zones = [e for e in store.entities.values() if e["id"].split(":")[4] == "zone"]
    assert [z["id"] for z in zones] == [ZN_ID]
    zone = store.entities[ZN_ID]
    assert zone["hasAgriParcel"]["object"] == PARCEL
    assert zone["hasAgriManagementZone"]["object"] == ZONE
    assert "hasAgriParcelZone" not in zone
    assert _prop(zone, "productID") == S1_A and _prop(zone, "sensingDate") == "2026-06-21T05:43:21Z"
    assert _analytics(zone) == {"backscatterVV": "-9.0000", "backscatterVH": "-9.0000",
                                "pixelCount": "30", "validPixelFraction": "0.9500"}
    assert zone["processedBbox"]["value"] == GEOM
    _assert_parse({k: v for k, v in store.entities.items() if k == ZN_ID}, {ZN_ID: ("s1-backscatter", DAY)})
    # the old form still writes what it wrote before (both zones are its business, not this task's)
    assert len(_old_entities(result)) >= 3


def test_zone_same_day_larger_coverage_wins(store, rl):
    take_b = rl.radar_take(S1_B, {"scene_id": S1_B, "sensing_date": "2026-06-21"})
    target = rl.zone_target(PARCEL, ZONE, GEOM)
    both = lambda n: {"VV": {"mean": -9.0, "pixel_count": n, "valid_fraction": n / 32},  # noqa: E731
                      "VH": {"mean": -9.0, "pixel_count": n, "valid_fraction": n / 32}}
    run_s1(zones=[CURRENT_ZONE])
    assert rl.write_backscatter(TENANT, target, take_b, both(10)) == rl.SKIP
    assert rl.write_backscatter(TENANT, target, take_b, both(31)) == rl.REPLACE
    assert _prop(store.entities[ZN_ID], "productID") == S1_B
    _assert_parse({k: v for k, v in store.entities.items() if k == ZN_ID}, {ZN_ID: ("s1-backscatter", DAY)})


# ── processors and the calibrated clip give their metadata ──────────────────

def test_lst_processors_return_the_catalogue_item():
    from app.services import clms_lst_processor, lst_processor

    item = {"id": LANDSAT_ID, "geometry": FOOT, "assets": {"ST_B10": {"href": "https://x/y.tif"}},
            "properties": {"datetime": "2026-06-20T10:50:12Z", "platform": "landsat-9"}}
    stats = {"mean": 30.0, "min": 29.0, "max": 31.0, "std": 0.5, "pixel_count": 4}
    with patch.object(lst_processor, "search_latest_lst_scene", return_value=item), \
            patch.object(lst_processor, "_download_asset"), \
            patch.object(lst_processor, "compute_lst_zonal_stats", return_value=dict(stats)):
        triple = lst_processor.process_parcel_lst(0.5, 0.5, GEOM)
        full = lst_processor.process_parcel_lst_item(0.5, 0.5, GEOM)
    assert triple == (stats, "2026-06-20", LANDSAT_ID)  # callers keep their shape
    assert full[:3] == triple and full[3]["properties"]["platform"] == "landsat-9" and full[3]["geometry"] == FOOT

    clms = {"id": CLMS_ID, "geometry": FOOT, "assets": {"LST": {"href": "https://x/z.tif"}},
            "properties": {"datetime": "2026-06-20T11:00:00Z", "product:type": "land_surface_temperature"}}
    resp = MagicMock()
    resp.__enter__.return_value.iter_content.return_value = [b"x"]
    with patch.object(clms_lst_processor, "search_latest_clms_lst", return_value=clms), \
            patch.object(clms_lst_processor.requests, "get", return_value=resp), \
            patch.object(clms_lst_processor, "compute_clms_lst_zonal", return_value=dict(stats)):
        triple = clms_lst_processor.process_clms_lst(0.5, 0.5, GEOM)
        full = clms_lst_processor.process_clms_lst_item(0.5, 0.5, GEOM)
    assert triple == (stats, "2026-06-20T11:00:00Z", CLMS_ID)
    assert full[:3] == triple and full[3]["properties"]["product:type"] == "land_surface_temperature"


# ── fix round 1 ─────────────────────────────────────────────────────────────

LANDSAT_B = "LC08_L2SP_200031_20260620_20260621_02_T1_ST"


def test_s1_product_type_is_the_items_and_omitted_without_it(store, rl):
    assert rl.radar_take(S1_A, GRD_PARAMS).scene.product_type == "IW_GRDH_1S"
    bare = {k: v for k, v in GRD_PARAMS.items() if k != "scene_product_type"}
    target = rl.parcel_target(PARCEL, GEOM)
    assert rl.write_backscatter(TENANT, target, rl.radar_take(S1_A, bare), {"VV": {
        "mean": -10.0, "pixel_count": 5, "valid_fraction": 0.9}}) == rl.CREATE
    assert "productType" not in store.entities[BS_ID]


def test_landsat_has_no_id_token_fallback_and_takes_the_items_code(rl):
    item = {"id": LANDSAT_ID, "geometry": FOOT, "properties": {"datetime": "2026-06-20T10:50:12Z"}}
    assert rl.landsat_take(item).scene.product_type == ""  # the id says L2SP; the item did not
    item["properties"]["product:type"] = "L2SP-from-catalogue"
    assert rl.landsat_take(item).scene.product_type == "L2SP-from-catalogue"


@pytest.mark.parametrize("enabled", [True, False])
def test_task_makes_no_extra_catalogue_request(store, rl, monkeypatch, enabled):
    from app.services.copernicus_client import CopernicusDataSpaceClient

    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT if enabled else "")
    props = {"datetime": SCENE_A["datetime"], "platform": "sentinel-1a", "sat:relative_orbit": 103,
             "sat:orbit_state": "descending", "sar:instrument_mode": "IW", "product:type": "IW_GRDH_1S"}
    item = {"id": S1_A, "sensing_date": "2026-06-21", "datetime": SCENE_A["datetime"], "geometry": FOOT,
            "properties": props, "assets": {
                "vv": {"href": "s3://eodata/a/vv.tiff"}, "vh": {"href": "s3://eodata/a/vh.tiff"},
                "schema-calibration-vv": {"href": "s3://eodata/a/cv.xml"},
                "schema-calibration-vh": {"href": "s3://eodata/a/ch.xml"}}}
    calls = []
    real = CopernicusDataSpaceClient("i", "s")
    real.get_scene_item = lambda scene_id, collection="sentinel-2-l2a": calls.append(scene_id) or item
    real._get_s3_client = MagicMock()
    old_params = {k: v for k, v in GRD_PARAMS.items() if k not in PARAM_KEYS}
    result = run_s1(old_params, client=real)
    assert len(calls) == 1  # bands, calibration and the metadata share one item
    assert result.smi.call_args.kwargs["orbit_direction"] == "DESCENDING"
    assert (BS_ID in store.entities) is enabled
    if enabled:
        assert _prop(store.entities[BS_ID], "productType") == "IW_GRDH_1S"


@pytest.mark.parametrize("which", ["landsat", "clms"])
def test_lst_take_builder_failure_is_contained(store, rl, caplog, monkeypatch, which):
    from app.tasks import lst_tasks

    item = {"id": LANDSAT_ID, "geometry": FOOT, "properties": {"datetime": "2026-06-20T10:50:12Z"}}
    stats = {"mean": 30.0, "min": 29.0, "max": 31.0, "std": 0.5, "pixel_count": 4}
    old = []
    builder = MagicMock(side_effect=RuntimeError("boom"))
    patches = [patch.object(fi, "_upsert_eoproduct_entity",
                            side_effect=lambda t, e: old.append(e) or {"upserted": 1}),
               patch.object(lst_tasks, "_auth_headers", return_value={}),
               patch.object(lst_tasks.satimg, "landsat_take", builder),
               patch.object(lst_tasks.satimg, "clms_take", builder)]
    task, name, ret = ((lst_tasks.process_parcel_lst_task, "process_parcel_lst_item",
                        (dict(stats), "2026-06-20", LANDSAT_ID, item)) if which == "landsat" else
                       (lst_tasks.process_parcel_clms_lst_task, "process_clms_lst_item",
                        (dict(stats), "2026-06-20T11:00:00Z", CLMS_ID, item)))
    patches.append(patch.object(lst_tasks, name, return_value=ret))
    with ExitStack() as stack:
        for pt in patches:
            stack.enter_context(pt)
        with caplog.at_level(logging.ERROR):
            result = task.run(TENANT, PARCEL, 0.5, 0.5, GEOM)  # a retry would raise here
        assert result["status"] == "ok" and len(old) == 1 and builder.call_count == 1
        assert any("eo_satimg" in r.getMessage() for r in caplog.records)
        # switched off: the builder is not called and nothing is logged
        builder.reset_mock()
        caplog.clear()
        monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", "")
        with caplog.at_level(logging.INFO):
            task.run(TENANT, PARCEL, 0.5, 0.5, GEOM)
        builder.assert_not_called()
        assert not [r for r in caplog.records if "eo_satimg" in r.getMessage()]


def test_radar_take_failure_is_contained(store, rl, caplog):
    reference = run_s1()
    store.entities.clear()
    with patch.object(rl, "radar_take", side_effect=RuntimeError("boom")), caplog.at_level(logging.ERROR):
        result = run_s1()
    assert _old_entities(result) == _old_entities(reference)
    assert not store.entities
    result.job.mark_completed.assert_called_once()
    assert any("eo_satimg" in r.getMessage() for r in caplog.records)


def test_new_form_is_written_after_the_old_form(store, rl):
    events = []
    real_upsert = store.upsert
    with patch.object(fi, "upsert_eo_satellite_imagery",
                      side_effect=lambda t, e: events.append(("new", e["id"])) or real_upsert(t, e)):
        run_s1(zones=[CURRENT_ZONE], events=events)
    kinds = [k for k, _ in events]
    assert kinds[0] == "old" and "new" in kinds
    first_new = kinds.index("new")
    # the parcel GRD product (old) is written before any new entity
    assert any(e.startswith("urn:ngsi-ld:EOProduct") and ":GRD:" in e for _, e in events[:first_new])


def test_merge_keeps_the_smaller_coverage_when_a_stored_polarisation_is_kept(store, rl):
    target = rl.parcel_target(PARCEL, GEOM)
    take = rl.radar_take(S1_A, GRD_PARAMS)
    both = {"VV": {"mean": -10.0, "pixel_count": 50, "valid_fraction": 0.9},
            "VH": {"mean": -17.0, "pixel_count": 40, "valid_fraction": 0.8}}
    assert rl.write_backscatter(TENANT, target, take, both) == rl.CREATE
    # the rerun brings VV only, with more pixels: VH from the first run is kept, so coverage is the smaller
    assert rl.write_backscatter(TENANT, target, take, {"VV": {
        "mean": -11.0, "pixel_count": 60, "valid_fraction": 0.95}}) == rl.MERGE
    a = _analytics(store.entities[BS_ID])
    assert a["backscatterVH"] == "-17.0000" and a["backscatterVV"] == "-11.0000"
    assert a["pixelCount"] == "40" and a["validPixelFraction"] == "0.8000"
    # a rerun that brings both takes the new coverage as it is
    assert rl.write_backscatter(TENANT, target, take, {
        "VV": {"mean": -11.0, "pixel_count": 60, "valid_fraction": 0.95},
        "VH": {"mean": -17.5, "pixel_count": 70, "valid_fraction": 0.97}}) == rl.MERGE
    assert _analytics(store.entities[BS_ID])["pixelCount"] == "60"


def test_landsat_replacement_parses_and_clms_tie_goes_to_the_latest_hour(store, rl):
    target = rl.parcel_target(PARCEL, GEOM)
    item_a = {"id": LANDSAT_ID, "geometry": FOOT, "properties": {"datetime": "2026-06-20T10:50:12Z",
                                                                 "platform": "landsat-9"}}
    item_b = {"id": LANDSAT_B, "geometry": FOOT, "properties": {"datetime": "2026-06-20T10:51:00Z",
                                                                "platform": "landsat-8"}}
    stats = lambda n: {"mean": 30.0, "min": 29.0, "max": 31.0, "std": 0.5, "pixel_count": n}  # noqa: E731
    assert rl.write_lst(TENANT, target, rl.landsat_take(item_a), stats(4)) == rl.CREATE
    assert rl.write_lst(TENANT, target, rl.landsat_take(item_b), stats(9)) == rl.REPLACE
    assert _prop(store.entities[LS_ID], "platformName") == "LANDSAT_8"
    _assert_parse({LS_ID: store.entities[LS_ID]}, {LS_ID: ("landsat-lst", date(2026, 6, 20))})
    c1 = {"id": CLMS_ID, "geometry": FOOT, "properties": {"datetime": "2026-06-20T11:00:00Z"}}
    c2 = {"id": CLMS_ID + "-b", "geometry": FOOT, "properties": {"datetime": "2026-06-20T12:00:00Z"}}
    assert rl.write_lst(TENANT, target, rl.clms_take(c1), stats(2)) == rl.CREATE
    assert rl.write_lst(TENANT, target, rl.clms_take(c2), stats(2)) == rl.REPLACE  # equal coverage: the latest hour
    assert _prop(store.entities[CL_ID], "productID") == CLMS_ID + "-b"
    assert _prop(store.entities[CL_ID], "sensingDate") == "2026-06-20T12:00:00Z"
