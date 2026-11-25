"""eo_satimg_backfill: parcel EOProduct -> EOSatelliteImagery, read with the real SDK reader on both shapes."""
import copy
import io
import json
import os
import sys
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
for _m in ("rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows", "rasterio.control",
           "rasterio.transform", "rasterio.mask", "simpleeval"):
    sys.modules.setdefault(_m, MagicMock())

import pytest
from nkz_platform_sdk import parse_eo_entities

from app.jobs import eo_satimg_backfill as bf
from app.services import fiware_integration as fi
from tests import test_eo_satimg_optical_writers as ow

TENANT = "acme"
PARCEL = "urn:ngsi-ld:AgriParcel:parcel-long-name-1234"
PARCEL2 = "urn:ngsi-ld:AgriParcel:parcel-two"
DAY = date(2026, 6, 21)
PID_A, PID_B = ow.PID_A, ow.PID_B
S1_A = "S1A_IW_GRDH_1SDV_20260621T054321_20260621T054346_059700_0758AB_1A2B"
LANDSAT_ID = "LC09_L2SP_200031_20260620_20260621_02_T1_ST"
U_A = "22222222-2222-2222-2222-222222222222"
U_B = "33333333-3333-3333-3333-333333333333"
GEOM, FOOT = ow.GEOM, ow.FOOT
NKZ = "https://nkz-os.org/ns/"
AGRI = "https://smartdatamodels.org/dataModel.Agrifood/"
NOON = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def prop(value, **sub):
    out = {"type": "Property", "value": value}
    out.update({NKZ + k: {"type": "Property", "value": v} for k, v in sub.items()})
    return out


def url(parcel, scene_uuid, index):
    return f"s3://bucket/{TENANT}/entities/{parcel}/scenes/{scene_uuid}/{index}.tif"


def index_attr(value, parcel=PARCEL, scene_uuid=U_A, index="ndvi", with_url=True):
    sub = {"min": value - 0.3, "max": value + 0.3, "std": 0.1}
    if with_url:
        sub["rasterUrl"] = url(parcel, scene_uuid, index)
    return prop(value, **sub)


def legacy(attrs, parcel=PARCEL, day=DAY, short="parcel-long-name-1234", with_day=True, eid=None):
    """An EOProduct as the broker returns it WITHOUT a Link header (terms as full IRIs)."""
    entity = {"id": eid or f"urn:ngsi-ld:EOProduct:{TENANT}:{short[:10]}:{day.isoformat()}", "type": bf.EOPRODUCT_TYPE,
              AGRI + "hasAgriParcel": {"type": "Relationship", "object": parcel}}
    if with_day:
        entity[NKZ + "sensingDate"] = prop(day.isoformat())
    entity.update(attrs)
    return entity


def optical(**extra):
    return legacy({NKZ + "ndvi": index_attr(0.61), NKZ + "evi": index_attr(0.41, index="evi"),
                   NKZ + "pixelCount": prop(40), NKZ + "cloudCoverPercentage": prop(99.0), **extra})


def fused():
    return optical(**{
        NKZ + "sarMoisture": prop(0.3, min=0.1, max=0.5, std=0.05), NKZ + "sarSceneId": prop(S1_A),
        NKZ + "sarChangeFlag": {"type": "Property", "value": "stable", NKZ + "confidence": prop(0.9),
                                NKZ + "deltaVV": prop(0.2)},
        NKZ + "lst": prop(21.5, min=19.0, max=24.0, std=1.0), NKZ + "lstSourceScene": prop(LANDSAT_ID)})


def job(index, scene=U_A, day=DAY, deleted=False, result=None, params=None, parcel=PARCEL, start=None, end=None,
        geometry=None, jid=None):
    res = {"index_type": index.upper(), "sensing_date": day.isoformat(), "scene_id": scene, "is_composite": False}
    res.update(result or {})
    return bf.JobRow(id=jid or str(uuid.uuid4()), index_type=index.lower(), deleted=deleted, result=res,
                     parameters=params or {"index_type": index.upper()}, start_date=start, end_date=end,
                     geometry=geometry)


class Src:
    def __init__(self, jobs=(), scenes=None, geometry=GEOM):
        self._jobs, self.geometry = list(jobs), geometry
        self._scenes = scenes if scenes is not None else {
            U_A: bf.SceneRow(PID_A, DAY, datetime(2026, 6, 21, 10, 50, 31, tzinfo=timezone.utc), FOOT, "12.5"),
            U_B: bf.SceneRow(PID_B, DAY, datetime(2026, 6, 21, 11, 6, 21, tzinfo=timezone.utc), FOOT, "30"),
        }

    def jobs(self, tenant, parcel):
        return [j for j in self._jobs if j is not None]

    def scene(self, tenant, scene_uuid):
        return self._scenes.get(str(scene_uuid))

    def parcel_geometry(self, tenant, parcel):
        return self.geometry


class FakeResponse:
    def __init__(self, body, headers):
        self._body, self.headers = body, headers

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class FakeClient:
    """Serves entities by full type IRI; asserts the read is the no-Link, full-IRI, counted one."""

    def __init__(self, tenant, legacy_entities=(), new_entities=(), skew=0, drift=False):
        self.tenant_id, self.skew, self.drift, self.calls = tenant, skew, drift, 0
        self.by_type = {bf.EOPRODUCT_TYPE: list(legacy_entities), bf.EOSATIMG_TYPE: list(new_entities)}
        self._session = self

    def _headers(self, content_type="application/ld+json"):
        assert content_type == "none"
        return {"NGSILD-Tenant": self.tenant_id, "Fiware-Service": self.tenant_id}

    def _url(self, path):
        return "http://orion" + path

    def get(self, url_, params=None, headers=None, timeout=None):
        assert "Link" not in headers and params["type"].startswith("https://smartdatamodels.org/")
        assert params["count"] == "true"
        self.calls += 1
        items = self.by_type[params["type"]]
        count = len(items) + self.skew + (self.calls if self.drift else 0)
        off, lim = params["offset"], params["limit"]
        return FakeResponse(copy.deepcopy(items[off:off + lim]), {bf.COUNT_HEADER: str(count)})


@pytest.fixture
def store(monkeypatch):
    s = ow.Store()
    monkeypatch.setattr(fi, "get_eo_satellite_imagery", s.get)
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", s.upsert)
    monkeypatch.delenv("EO_SATIMG_WRITE_TENANTS", raising=False)  # the job is explicit: not gated by the switch
    monkeypatch.setenv(bf.REVIEW_ENV, "")
    return s


def run(entities, src=None, args=(), tenant=TENANT, clock=None, client=None):
    client = client or FakeClient(tenant, entities)
    out = io.StringIO()
    code = bf.main(["--tenant", tenant, "--json", *args], sources=src or Src(), client_factory=lambda t: client,
                   clock=clock or (lambda: NOON), out=out)
    lines = [line for line in out.getvalue().splitlines() if line.startswith("{")]
    return code, (json.loads(lines[0]) if lines else None), out.getvalue()


def names(entity):
    return {i["name"]: i["value"] for i in entity["analytics"]["value"]}


def new_id(parcel, family, day=DAY):
    return f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:parcel:{parcel.split(':', 3)[3]}:{family}:{day.isoformat()}"


# ── the plan's seven ────────────────────────────────────────────────────────

def test_fused_optical_entity_splits_into_four(store):
    code, report, _ = run([fused()], Src([job("ndvi"), job("evi")]), ["--apply"])
    assert code == 0 and report["counters"]["created"] == 4 and report["counters"]["items"] == 4
    for family in ("s2-indices", "s1-soil-moisture", "s1-change", "landsat-lst"):
        assert new_id(PARCEL, family) in store.entities, family
    optical_entity = store.entities[new_id(PARCEL, "s2-indices")]
    assert optical_entity["productID"]["value"] == PID_A
    assert optical_entity["hasAgriParcel"]["object"] == PARCEL
    assert optical_entity["processedBbox"]["value"] == GEOM
    values = names(optical_entity)
    assert values["ndvi"] == "0.610000" and values["evi"] == "0.410000" and values["cloudCoverage"] == "99.00"  # the legacy value is kept
    assert report["counters"]["cloud_differs"] == 1
    assert optical_entity["localServerPath"]["value"] == f"s3://bucket/{TENANT}/entities/{PARCEL}/scenes/{U_A}"
    assert store.entities[new_id(PARCEL, "s1-soil-moisture")]["productID"]["value"] == S1_A
    lst = store.entities[new_id(PARCEL, "landsat-lst")]
    assert lst["productID"]["value"] == LANDSAT_ID and "productType" not in lst  # no catalogue item: omitted
    # the real reader reads what was written, with the values the legacy shape held
    by_family = {o.family: o for o in parse_eo_entities(list(store.entities.values()))}
    assert by_family["s2-indices"].values["ndvi"] == pytest.approx(0.61)
    assert by_family["s1-change"].values["changeFlag"] == "stable"
    assert by_family["landsat-lst"].values["lst"] == pytest.approx(21.5)


def test_grd_parcel_full_id_and_acquisition_date_to_sensing(store):
    grd = legacy({"productType": prop("GRD"), NKZ + "backscatterVV": prop(-12.3456), NKZ + "backscatterVH": prop(-18.5),
                  NKZ + "acquisitionDate": prop("2026-06-21T00:00:00Z")}, with_day=False,
                 eid=f"urn:ngsi-ld:EOProduct:{TENANT}:parcel-long-name-1234:GRD:2026-06-21")
    jobs = [job("sar-vv", params={"scene_id": S1_A, "index_type": "SAR-VV"}),
            job("sar-vh", params={"scene_id": S1_A, "index_type": "SAR-VH"})]
    code, report, _ = run([grd], Src(jobs), ["--apply"])
    assert code == 0 and report["counters"]["created"] == 1
    entity = store.entities[new_id(PARCEL, "s1-backscatter")]  # the FULL parcel id, not the 10-char one
    assert entity["hasAgriParcel"]["object"] == PARCEL
    assert entity["sensingDate"]["value"] == "2026-06-21"  # the day only: no invented time
    assert entity["productID"]["value"] == S1_A and entity["platformName"]["value"] == "S1A"
    assert "productType" not in entity  # the legacy 'GRD' is not the catalogue's code
    assert names(entity) == {"backscatterVH": "-18.5000", "backscatterVV": "-12.3456"}


@pytest.mark.parametrize("relation", [AGRI + "hasAgriParcelZone", AGRI + "hasAgriManagementZone"])
def test_zone_legacy_is_skipped_and_counted(store, relation):
    zone = optical()
    zone["id"] = f"urn:ngsi-ld:EOProduct:{TENANT}:parcel-long-name-1234:zone:L1:{DAY}"
    zone[relation] = {"type": "Relationship", "object": "urn:ngsi-ld:AgriManagementZone:parcel-long-name-1234:r1:L1"}
    code, report, _ = run([zone, optical()], Src([job("ndvi"), job("evi")]), ["--apply"])
    assert report["counters"]["zones_skipped"] == 1 and report["counters"]["created"] == 1
    assert not [e for e in store.entities if ":zone:" in e]


def test_missing_geometry_omits_processed_bbox_and_says_so(store):
    code, report, _ = run([optical()], Src([job("ndvi"), job("evi")], geometry=None), ["--apply"])
    entity = store.entities[new_id(PARCEL, "s2-indices")]
    assert "processedBbox" not in entity
    assert entity["resultDescription"]["value"].endswith("The processed geometry was not available.")
    assert report["counters"]["no_geometry"] == 1 and report["counters"]["created"] == 1


def test_geometry_the_analysis_used_wins_over_the_parcel_location(store):
    used = {"type": "Polygon", "coordinates": [[[5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 5.0]]]}
    run([optical()], Src([job("ndvi", geometry=used), job("evi")]), ["--apply"])
    assert store.entities[new_id(PARCEL, "s2-indices")]["processedBbox"]["value"] == used


def test_existing_new_entity_merged_not_degraded(store):
    from app.services import eo_satimg_optical as so
    from app.services.eo_satellite_imagery import build_entity
    scene = ow._scene(pid=PID_A, scene_uuid=uuid.UUID(U_A))
    take = so.scene_take(scene)
    stored = build_entity(tenant=TENANT, target=so.parcel_target(PARCEL, GEOM), family=take.family, sensing=take.sensing,
                          analytics={"ndvi": 0.6, "ndvi.min": 0.1, "pixelCount": 40}, scene=take.scene, raster=None,
                          result_description="stored description")
    store.upsert(TENANT, stored)
    before = copy.deepcopy(store.entities[stored["id"]])
    code, report, _ = run([optical()], Src([job("ndvi"), job("evi")]), ["--apply"])
    after = store.entities[stored["id"]]
    assert report["counters"]["merged"] == 1
    values = names(after)
    assert values["ndvi"] == "0.600000" and values["ndvi.min"] == "0.100000"  # stored: never overwritten
    assert values["evi"] == "0.410000"  # the whole missing index arrived
    assert after["sensingDate"] == before["sensingDate"] and after["resultDescription"] == before["resultDescription"]
    assert set(store.upserts[-1]) == {"id", "type", "analytics"}  # nothing but the full list was sent


def test_dry_run_writes_nothing(store):
    code, report, text = run([fused()], Src([job("ndvi"), job("evi")]))
    assert code == 0 and report["mode"] == "dry-run" and report["counters"]["created"] == 4
    assert store.upserts == [] and store.entities == {}
    buffer = io.StringIO()
    bf.main(["--tenant", TENANT], sources=Src([job("ndvi")]), client_factory=lambda t: FakeClient(t, [fused()]),
            clock=lambda: NOON, out=buffer)
    text = buffer.getvalue()
    assert "dry-run" in text and "created=" in text  # the plain one-line-per-tenant form


def test_second_apply_changes_nothing(store):
    entities, src = [fused()], Src([job("ndvi"), job("evi")])
    run(entities, src, ["--apply"])
    snapshot, writes = copy.deepcopy(store.entities), len(store.upserts)
    code, report, _ = run(entities, src, ["--apply"])
    assert report["counters"]["unchanged"] == 4 and "created" not in report["counters"]
    assert len(store.upserts) == writes and store.entities == snapshot


# ── the controller's bindings ───────────────────────────────────────────────

def test_pages_mismatch_aborts_the_tenant_and_writes_nothing(store):
    code, report, text = run(None, client=FakeClient(TENANT, [optical()], skew=1), args=["--apply"])
    assert code == 1 and "ABORTED" in text and store.upserts == []
    code, _, text = run(None, client=FakeClient(TENANT, [optical()], drift=True), args=["--apply"])
    assert code == 1 and "ABORTED" in text and store.upserts == []


def test_pages_are_followed_and_checked(store, monkeypatch):
    monkeypatch.setattr(bf, "PAGE", 2)
    many = [optical() for _ in range(5)]
    for i, e in enumerate(many):
        e["id"] = e["id"] + str(i)
    assert len(bf.read_all(FakeClient(TENANT, many), bf.EOPRODUCT_TYPE)) == 5


def test_review_tenant_is_refused_without_the_flag_and_apply_needs_the_variable(store, monkeypatch):
    monkeypatch.setenv(bf.REVIEW_ENV, "rev-tenant,other")
    client = FakeClient("rev-tenant", [optical()])
    code, _, text = run(None, tenant="rev-tenant", client=client, args=["--apply"])
    assert code == 2 and "--allow-review" in text and client.calls == 0 and store.upserts == []
    code, _, _ = run(None, tenant="rev-tenant", client=client, args=["--allow-review"])
    assert code == 0 and client.calls > 0
    monkeypatch.delenv(bf.REVIEW_ENV)
    code, _, text = run([optical()], args=["--apply"])
    assert code == 2 and bf.REVIEW_ENV in text and store.upserts == []


def test_scene_is_resolved_per_index_from_the_raster_then_the_job(store):
    entity = legacy({NKZ + "ndvi": index_attr(0.61, scene_uuid=U_A), NKZ + "evi": index_attr(0.4, index="evi", with_url=False)})
    run([entity], Src([job("ndvi", scene=U_B), job("evi", scene=U_A)]), ["--apply"])
    only = store.entities[new_id(PARCEL, "s2-indices")]
    assert only["productID"]["value"] == PID_A  # ndvi: the raster folder wins over its job; evi: its job says A too
    assert set(names(only)) >= {"ndvi", "evi"}


def test_a_two_scene_day_is_left_whole_for_the_owner_and_nothing_is_written(store):
    entity = legacy({NKZ + "evi": index_attr(0.4, scene_uuid=U_B, index="evi"), NKZ + "ndvi": index_attr(0.6, scene_uuid=U_A)})
    code, report, _ = run([entity], Src([job("ndvi", scene=U_A), job("evi", scene=U_B)]), ["--apply"])
    assert store.entities == {} and store.upserts == []  # the legacy stays visible whole
    assert report["counters"]["left_legacy"] == 1 and "second_scene_dropped" not in report["counters"]
    left = report["left_legacy"][0]
    assert left["reason"] == "two_scenes" and left["indices"] == ["evi", "ndvi"] and left["family"] == "s2-indices"


def test_an_unresolvable_index_leaves_the_whole_optical_part_but_not_the_other_families(store):
    entity = fused()
    entity[NKZ + "ndre"] = prop(0.5, min=0.2, max=0.8, std=0.1,
                                rasterUrl=f"s3://bucket/{TENANT}/entities/{PARCEL}/composites/some-job-id/ndre.tif")
    code, report, _ = run([entity], Src([job("ndvi"), job("evi"), job("ndre", scene=None)]), ["--apply"])
    assert report["counters"]["composite_no_window"] == 1 and report["counters"]["left_legacy"] >= 1
    assert new_id(PARCEL, "s2-indices") not in store.entities  # ndvi/evi would hide ndre: left whole
    assert new_id(PARCEL, "s1-change") in store.entities and new_id(PARCEL, "landsat-lst") in store.entities
    assert any("ndre" in entry["indices"] and entry["reason"] == "composite_no_window" for entry in report["left_legacy"])


def test_composite_by_raster_folder_and_by_job_window_is_dated_by_its_end(store):
    window = "2026-06-01_2026-06-15"
    comp_url = f"s3://bucket/{TENANT}/entities/{PARCEL}/composites/{window}/ndre.tif"
    entity = legacy({NKZ + "ndvi": index_attr(0.6), NKZ + "ndre": prop(0.5, min=0.2, max=0.8, std=0.1, rasterUrl=comp_url),
                     NKZ + "ndmi": prop(0.3, min=0.1, max=0.4, std=0.1)})
    composite_job = job("ndmi", scene=None, result={"is_composite": True, "date_range": {"start": "2026-06-02", "end": "2026-06-16"},
                                                    "scene_id": None})
    code, report, _ = run([entity], Src([job("ndvi"), job("ndre", scene=None, result={"is_composite": True}), composite_job]),
                          ["--apply"])
    assert "composite_no_window" not in report["counters"]
    assert new_id(PARCEL, "s2-indices") in store.entities  # ndvi stays a single scene
    ndre = store.entities[new_id(PARCEL, "s2-composite", date(2026, 6, 15))]
    assert "2026-06-01 to 2026-06-15" in ndre["resultDescription"]["value"] and "productID" not in ndre
    ndmi = store.entities[new_id(PARCEL, "s2-composite", date(2026, 6, 16))]  # the job's window end
    assert "2026-06-02 to 2026-06-16" in ndmi["resultDescription"]["value"]


def test_composite_without_a_window_is_counted_not_guessed(store):
    entity = legacy({NKZ + "ndre": prop(0.5, min=0.2, max=0.8, std=0.1,
                                        rasterUrl=f"s3://bucket/{TENANT}/entities/{PARCEL}/composites/some-job-id/ndre.tif")})
    code, report, _ = run([entity], Src([job("ndre", scene=None)]), ["--apply"])
    assert report["counters"]["composite_no_window"] == 1 and store.entities == {}


def test_deleted_job_is_not_resurrected_and_no_job_is_converted_and_counted(store):
    entity = optical()
    code, report, _ = run([entity], Src([job("ndvi", deleted=True)]), ["--apply"])
    values = names(store.entities[new_id(PARCEL, "s2-indices")])
    assert "ndvi" not in values and "evi" in values  # ndvi's only job is deleted; evi has none at all
    assert report["counters"]["job_deleted"] == 1 and report["counters"]["no_job"] == 1


def test_merge_only_for_the_same_product_id(store):
    from app.services import eo_satimg_optical as so
    from app.services.eo_satellite_imagery import build_entity
    other = ow._scene(pid=PID_B, scene_uuid=uuid.UUID(U_B))
    take = so.scene_take(other)
    stored = build_entity(tenant=TENANT, target=so.parcel_target(PARCEL, GEOM), family=take.family, sensing=take.sensing,
                          analytics={"ndvi": 0.9, "ndvi.min": 0.8}, scene=take.scene, raster=None, result_description="x")
    store.upsert(TENANT, stored)
    writes = len(store.upserts)
    code, report, _ = run([optical()], Src([job("ndvi"), job("evi")]), ["--apply"])
    assert report["counters"]["other_take"] == 1 and len(store.upserts) == writes
    assert names(store.entities[stored["id"]]) == {"ndvi": "0.900000", "ndvi.min": "0.800000"}


def test_verify_is_clean_after_apply_and_catches_a_missing_index_and_a_different_value(store):
    entities, src = [optical()], Src([job("ndvi"), job("evi")])
    run(entities, src, ["--apply"])
    code, report, _ = run(entities, src, ["--verify"], client=FakeClient(TENANT, entities, list(store.entities.values())))
    assert code == 0 and report["diffs"] == [] and report["families"]["s2-indices"]["converted"] == 1
    stored = store.entities[new_id(PARCEL, "s2-indices")]
    items = stored["analytics"]["value"]
    stored["analytics"]["value"] = [i for i in items if i["name"] != "evi"]
    for i in stored["analytics"]["value"]:
        if i["name"] == "ndvi":
            i["value"] = "0.500000"
    code, report, text = run(entities, src, ["--verify"], client=FakeClient(TENANT, entities, list(store.entities.values())))
    assert code == 1
    assert {d["kind"] for d in report["diffs"]} == {"missing_index", "value_differs"}
    assert run(entities, src, ["--verify"], client=FakeClient(TENANT, entities, []))[0] == 1  # nothing written: missing


def test_apply_inside_the_nightly_window_is_refused_and_a_run_that_reaches_it_stops(store):
    at_two = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)
    code, _, text = run([optical()], args=["--apply"], clock=lambda: at_two)
    assert code == 2 and "01:15" in text and store.upserts == []
    times = iter([datetime(2026, 11, 24, 0, 30, tzinfo=timezone.utc)] * 2)
    clock = lambda: next(times, datetime(2026, 11, 24, 1, 20, tzinfo=timezone.utc))
    code, _, text = run([optical()], Src([job("ndvi"), job("evi")]), ["--apply"], clock=clock)
    assert code == 2 and "STOPPED" in text and store.upserts == []
    # a dry run is a read: it is not refused inside the window
    assert run([optical()], args=[], clock=lambda: at_two)[0] == 0


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class FlakyDb:
    def __init__(self):
        self.savepoints = self.rolled_back = self.queries = 0

    def begin_nested(self):
        import contextlib
        db = self

        @contextlib.contextmanager
        def nested():
            db.savepoints += 1
            try:
                yield
            except Exception:
                db.rolled_back += 1
                raise
        return nested()

    def execute(self, *a, **k):
        pass

    def query(self, *a):
        self.queries += 1
        if self.queries == 1:
            raise RuntimeError("db down")
        return _Q([])


def test_a_failing_query_does_not_poison_the_session_and_the_run_goes_on(store):
    db = FlakyDb()
    sql = bf.SqlSources(db)
    with pytest.raises(RuntimeError):
        sql.jobs(TENANT, PARCEL)
    assert sql.jobs(TENANT, PARCEL) == [] and (db.savepoints, db.rolled_back) == (2, 1)

    class FailsOnce(Src):
        def jobs(self, tenant, parcel):
            if parcel == PARCEL:
                raise RuntimeError("db down")
            return super().jobs(tenant, parcel)
    second = legacy({NKZ + "ndvi": index_attr(0.6, parcel=PARCEL2), NKZ + "evi": index_attr(0.4, parcel=PARCEL2, index="evi")},
                    parcel=PARCEL2, short="parcel-two")
    code, report, _ = run([optical(), second], FailsOnce([job("ndvi"), job("evi")]), ["--apply"])
    assert code == 1 and report["counters"]["errors"] == 1 and report["counters"]["created"] == 1
    assert new_id(PARCEL2, "s2-indices") in store.entities


def test_shells_and_unconvertible_are_counted_and_left_alone(store):
    shell = legacy({})
    no_pixels = legacy({NKZ + "ndvi": index_attr(0.0), NKZ + "pixelCount": prop(0)})
    no_pixels["id"] += "-b"
    single_pol = legacy({NKZ + "backscatterVV": prop(-12.0), "productType": prop("GRD")}, day=date(2026, 6, 22))
    code, report, text = run([shell, no_pixels, single_pol], Src([job("ndvi")]), ["--apply"])
    assert report["counters"]["shells"] == 1 and report["counters"]["unconvertible"] == 2
    reasons = sorted(u["reason"] for u in report["unconvertible"])
    assert reasons == ["fewer_than_2_analytics", "no_valid_pixels"] and store.entities == {}
    assert TENANT not in json.dumps(report)  # no tenant id in what is reported


def test_clms_prefix_is_stripped_from_the_product_id(store):
    entity = legacy({NKZ + "lst": prop(30.0, min=29.0, max=31.0, std=0.5), NKZ + "lstSourceScene": prop("CLMS:abc-123")})
    run([entity], args=["--apply"])
    stored = store.entities[new_id(PARCEL, "clms-lst")]
    assert stored["productID"]["value"] == "abc-123" and "platformName" not in stored


def test_sar_with_two_passes_the_same_day_is_not_guessed(store):
    grd = legacy({NKZ + "backscatterVV": prop(-12.0), NKZ + "backscatterVH": prop(-18.0)})
    jobs = [job("sar-vv", params={"scene_id": S1_A}), job("sar-vv", params={"scene_id": "S1C_IW_GRDH_other"})]
    code, report, _ = run([grd], Src(jobs), ["--apply"])
    assert report["counters"]["ambiguous_scene"] == 1
    assert "productID" not in store.entities[new_id(PARCEL, "s1-backscatter")]


# ── review round: leaks, ambiguity, honest descriptions, verify coverage, SQL rows ──

def test_take_wide_values_stay_with_the_only_take_of_the_whole_entity(store):
    comp_url = f"s3://bucket/{TENANT}/entities/{PARCEL}/composites/2026-06-01_2026-06-15/ndre.tif"
    entity = legacy({NKZ + "ndvi": index_attr(0.6), NKZ + "ndre": prop(0.5, min=0.2, max=0.8, std=0.1, rasterUrl=comp_url),
                     NKZ + "cloudCoverPercentage": prop(99.0), NKZ + "pixelCount": prop(40)})
    run([entity], Src([job("ndvi"), job("ndre", scene=None, result={"is_composite": True})]), ["--apply"])
    composite = names(store.entities[new_id(PARCEL, "s2-composite", date(2026, 6, 15))])
    assert "cloudCoverage" not in composite and "pixelCount" not in composite  # the scene's cloud is not the composite's
    assert names(store.entities[new_id(PARCEL, "s2-indices")])["cloudCoverage"] == "12.50"  # the scene's own


def _stats_job(index, scene, mean, **kw):
    return job(index, scene=scene, result={"statistics": {"mean": mean, "pixel_count": 40}}, **kw)


def test_several_live_jobs_the_same_day_are_told_apart_by_value_never_arbitrarily(store):
    entity = legacy({NKZ + "ndvi": index_attr(0.61, with_url=False)})
    jobs = [_stats_job("ndvi", U_B, 0.5), _stats_job("ndvi", U_A, 0.610000004)]
    run([entity], Src(jobs), ["--apply"])
    assert store.entities[new_id(PARCEL, "s2-indices")]["productID"]["value"] == PID_A


def test_a_tie_between_scenes_gives_no_scene_and_never_claims_the_statistical_api(store):
    entity = legacy({NKZ + "ndvi": index_attr(0.61, with_url=False)})
    code, report, _ = run([entity], Src([_stats_job("ndvi", U_A, 0.61), _stats_job("ndvi", U_B, 0.61)]), ["--apply"])
    stored = store.entities[new_id(PARCEL, "s2-indices")]
    assert report["counters"]["ambiguous_scene"] == 1 and "productID" not in stored
    assert "Statistical API" not in stored["resultDescription"]["value"]


def test_the_statistical_api_is_claimed_only_for_a_live_job_without_a_scene(store):
    entity = legacy({NKZ + "ndvi": index_attr(0.61, with_url=False)})
    run([entity], Src([job("ndvi", scene=None)]), ["--apply"])
    assert "Statistical API" in store.entities[new_id(PARCEL, "s2-indices")]["resultDescription"]["value"]
    store.entities.clear()
    run([entity], Src([]), ["--apply"])  # no job at all: nothing says which path produced it
    assert "Statistical API" not in store.entities[new_id(PARCEL, "s2-indices")]["resultDescription"]["value"]
    store.entities.clear()
    run([entity], Src([job("ndvi", scene="44444444-4444-4444-4444-444444444444")]), ["--apply"])  # scene row is gone
    assert "Statistical API" not in store.entities[new_id(PARCEL, "s2-indices")]["resultDescription"]["value"]


def test_a_job_that_wrote_nothing_and_a_raster_named_job_decide_correctly(store):
    entity = legacy({NKZ + "ndvi": index_attr(0.61, scene_uuid=U_A)})
    path = f"{TENANT}/entities/{PARCEL}/scenes/{U_A}/ndvi.tif"
    jobs = [job("ndvi", scene=U_A, deleted=True, result={"raster_path": path}),
            job("ndvi", scene=U_B, result={"skip_reason": "no_valid_pixels"}), job("ndvi", scene=U_B)]
    code, report, _ = run([entity], Src(jobs), ["--apply"])
    assert report["counters"]["job_deleted"] == 1 and store.entities == {}  # the raster's own job is deleted


def test_verify_also_compares_the_change_flag(store):
    entities, src = [fused()], Src([job("ndvi"), job("evi")])
    run(entities, src, ["--apply"])
    for item in store.entities[new_id(PARCEL, "s1-change")]["analytics"]["value"]:
        if item["name"] == "changeFlag":
            item["value"] = "changed"
    code, report, text = run(entities, src, ["--verify"], client=FakeClient(TENANT, entities, list(store.entities.values())))
    assert code == 1 and [d["name"] for d in report["diffs"]] == ["changeFlag"]
    plain = io.StringIO()
    bf.main(["--tenant", TENANT, "--verify"], sources=src, clock=lambda: NOON, out=plain,
            client_factory=lambda t: FakeClient(t, entities, list(store.entities.values())))
    assert "verify totals" in plain.getvalue() and "old=1" in plain.getvalue()
    assert "old=" in plain.getvalue() and "DIFF" in plain.getvalue()


class RowsDb(FlakyDb):
    def __init__(self, rows):
        super().__init__()
        self.queries, self.rows, self.criteria = 99, rows, []

    def query(self, *columns):
        rows, criteria = self.rows, self.criteria

        class Q(_Q):
            def filter(self_, *crit):
                criteria.extend(crit)
                return self_
        return Q(rows)


def _postgres(criteria):
    import sqlalchemy as sa
    from sqlalchemy.dialects import postgresql
    return str(sa.and_(*criteria).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_sql_sources_map_rows_and_the_queries_compile_for_postgres():
    rows = [SimpleNamespace(id=uuid.UUID(U_A), result={"index_type": "NDVI", "sensing_date": "2026-06-21"},
                            parameters={"bounds": GEOM}, deleted_at=None, start_date=None, end_date=None),
            SimpleNamespace(id=uuid.UUID(U_B), result={}, parameters={"index_type": "NDRE"},
                            deleted_at=datetime(2026, 7, 1, tzinfo=timezone.utc), start_date=date(2026, 6, 1),
                            end_date=date(2026, 6, 15))]
    db = RowsDb(rows)
    mapped = bf.SqlSources(db).jobs(TENANT, PARCEL)
    assert [(j.index_type, j.deleted, j.geometry) for j in mapped] == [("ndvi", False, GEOM), ("ndre", True, None)]
    assert mapped[1].start_date == date(2026, 6, 1)
    sql = _postgres(db.criteria)
    assert "vegetation_jobs.status = 'completed'" in sql and "deleted_at" not in sql  # deleted rows ARE listed
    scene_db = RowsDb([("S2A_x", DAY, None, "12.5", json.dumps(FOOT))])
    row = bf.SqlSources(scene_db).scene(TENANT, U_A)
    assert (row.scene_id, row.sensing_date, row.cloud_coverage) == ("S2A_x", DAY, "12.5")
    assert row.footprint == json.dumps(FOOT) and "vegetation_scenes.id" in _postgres(scene_db.criteria)
    assert RowsDb([]).rows == [] and bf.SqlSources(RowsDb([])).scene(TENANT, U_A) is None


# ── fix round 1, item 1: verify compares every name, lists other takes, reports unplanned ──

def _applied(entities=None, jobs=None):
    entities = entities or [optical()]
    src = Src(jobs or [job("ndvi"), job("evi")])
    return entities, src


def _verify(entities, src, store, extra=(), args=()):
    return run(entities, src, ["--verify", *args], client=FakeClient(TENANT, entities, [*store.entities.values(), *extra]))


def test_verify_fails_on_a_missing_statistic_and_a_different_cloud_cover(store):
    entities, src = _applied()
    run(entities, src, ["--apply"])
    stored = store.entities[new_id(PARCEL, "s2-indices")]
    stored["analytics"]["value"] = [i for i in stored["analytics"]["value"] if i["name"] != "ndvi.min"]
    for i in stored["analytics"]["value"]:
        if i["name"] == "cloudCoverage":
            i["value"] = "55.00"
    code, report, _ = _verify(entities, src, store)
    assert code == 1
    assert {(d["kind"], d["name"]) for d in report["diffs"]} == {("missing_value", "ndvi.min"), ("value_differs", "cloudCoverage")}


def test_verify_lists_a_wrong_scene_with_expected_and_found_and_accepts_it_only_on_request(store):
    entities, src = _applied()
    run(entities, src, ["--apply"])
    store.entities[new_id(PARCEL, "s2-indices")]["productID"]["value"] = PID_B
    code, report, _ = _verify(entities, src, store)
    other = [d for d in report["diffs"] if d["kind"] == "other_take"]
    assert code == 1 and other[0]["expected"] == PID_A and other[0]["found"] == PID_B
    code, report, _ = _verify(entities, src, store, args=["--accept-other-take"])
    assert code == 0 and report["families"]["s2-indices"]["skipped"]["other_take"] == 1


def test_verify_counts_unplanned_entities_without_failing(store):
    entities, src = _applied()
    run(entities, src, ["--apply"])
    extra = copy.deepcopy(store.entities[new_id(PARCEL, "s2-indices")])
    extra["id"] = new_id(PARCEL, "s2-indices", date(2026, 7, 1))
    extra["sensingDate"]["value"] = "2026-07-01"
    code, report, _ = _verify(entities, src, store, extra=[extra])
    assert code == 0 and len(report["unplanned"]) == 1 and report["unplanned"][0]["day"] == "2026-07-01"


# ── fix round 1, item 3: take-wide values ──

def test_wide_values_are_omitted_when_an_index_was_dropped_for_a_deleted_job(store):
    code, report, _ = run([optical()], Src([job("ndvi", deleted=True), job("evi")]), ["--apply"])
    values = names(store.entities[new_id(PARCEL, "s2-indices")])
    assert "ndvi" not in values and "pixelCount" not in values  # the count may belong to the hidden index
    assert values["cloudCoverage"] == "12.50"  # the scene's own, the legacy's being unattributable
    assert report["counters"]["wide_omitted"] == 1


def test_legacy_cloud_cover_equal_to_the_scene_is_not_a_difference(store):
    entity = legacy({NKZ + "ndvi": index_attr(0.6), NKZ + "evi": index_attr(0.4, index="evi"),
                     NKZ + "cloudCoverPercentage": prop(12.5)})
    code, report, _ = run([entity], Src([job("ndvi"), job("evi")]), ["--apply"])
    assert names(store.entities[new_id(PARCEL, "s2-indices")])["cloudCoverage"] == "12.50"
    assert "cloud_differs" not in report["counters"]


def test_a_composite_never_gets_a_scenes_cloud_cover(store):
    comp = f"s3://bucket/{TENANT}/entities/{PARCEL}/composites/2026-06-01_2026-06-15/ndre.tif"
    entity = legacy({NKZ + "ndre": prop(0.5, min=0.2, max=0.8, std=0.1, rasterUrl=comp)})
    run([entity], Src([job("ndre", scene=U_A, result={"is_composite": True})]), ["--apply"])
    assert "cloudCoverage" not in names(store.entities[new_id(PARCEL, "s2-composite", date(2026, 6, 15))])


# ── fix round 1, item 4: minors ──

@pytest.mark.parametrize("listed,asked", [("Rev-Tenant", "rev-tenant"), ("rev-tenant", "REV-TENANT")])
def test_review_guard_ignores_case_on_both_sides(store, monkeypatch, listed, asked):
    monkeypatch.setenv(bf.REVIEW_ENV, listed)
    client = FakeClient(asked, [optical()])
    code, _, text = run(None, tenant=asked, client=client)
    assert code == 2 and "--allow-review" in text and client.calls == 0


def test_dry_run_and_verify_warn_when_the_guard_variable_is_unset(store, monkeypatch):
    monkeypatch.delenv(bf.REVIEW_ENV)
    assert "review guard" in run([optical()], Src([job("ndvi")]))[2]
    assert "review guard" in run([optical()], Src([job("ndvi")]), ["--verify"], client=FakeClient(TENANT, [optical()]))[2]


def test_a_tenant_stops_after_five_consecutive_write_errors(store, monkeypatch):
    attempts = []
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", lambda t, e: attempts.append(e["id"]))  # always fails
    entities = []
    for n in range(8):
        parcel = f"urn:ngsi-ld:AgriParcel:p{n}"
        entities.append(legacy({NKZ + "ndvi": index_attr(0.6, parcel=parcel), NKZ + "evi": index_attr(0.4, parcel=parcel, index="evi")},
                               parcel=parcel, short=f"p{n}"))
    code, report, _ = run(entities, Src([job("ndvi"), job("evi")]), ["--apply"])
    assert code == 1 and len(attempts) == 5 and report["counters"]["stopped_on_errors"] == 1


def test_a_window_that_opens_mid_run_prints_the_counters_before_stopping(store):
    times = iter([datetime(2026, 11, 24, 0, 30, tzinfo=timezone.utc)] * 2)
    clock = lambda: next(times, datetime(2026, 11, 24, 1, 20, tzinfo=timezone.utc))
    code, _, text = run([optical()], Src([job("ndvi"), job("evi")]), ["--apply"], clock=clock)
    assert code == 2 and "STOPPED" in text and "old=1" in text and "items=1" in text


def test_a_dry_run_counts_each_new_id_once(store):
    both = [legacy({NKZ + "backscatterVV": prop(-12.0), NKZ + "backscatterVH": prop(-18.0)}, eid=f"urn:ngsi-ld:EOProduct:{TENANT}:{n}")
            for n in ("a", "b")]
    code, report, _ = run(both, Src([]))
    assert report["counters"]["created"] == 1 and report["counters"]["duplicate_id"] == 1
