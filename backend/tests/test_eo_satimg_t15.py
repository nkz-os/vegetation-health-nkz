"""Whole-phase review fixes (T1.5): optical winner per take, sync upserts, delete guards, retry, baseline, radar/LST."""
import logging
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
for _m in ("rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows", "rasterio.control",
           "rasterio.transform", "rasterio.mask", "simpleeval"):
    sys.modules.setdefault(_m, MagicMock())

import pytest

from tests import test_eo_satimg_sar_lst_writers as sl  # noqa: E402,F401
from app.services import eo_satimg_optical as so
from app.services import fiware_integration as fi
from tests import test_eo_satimg_optical_writers as ow

_REAL_UPSERT = fi.upsert_eo_satellite_imagery
PID_A, PID_B, PARCEL_ID, TENANT = ow.PID_A, ow.PID_B, ow.PARCEL_ID, ow.TENANT


@pytest.fixture
def store(monkeypatch):
    s = ow.Store()
    monkeypatch.setattr(fi, "get_eo_satellite_imagery", s.get)
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", s.upsert)
    monkeypatch.setattr(fi, "delete_eo_satellite_imagery", s.delete)
    monkeypatch.setattr(fi, "delete_eo_attribute", s.delete_attr)
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)
    monkeypatch.setenv("VEGETATION_COG_BUCKET", "bucket")
    return s


def _vals(store):
    return ow._analytics(store.entities[PARCEL_ID])


# ── I1 ──────────────────────────────────────────────────────────────────────

def test_i1_losing_scene_cannot_replace_through_another_index(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    ow._write_scene(PID_A, "NDMI", 0.4, 0.84)
    assert ow._write_scene(PID_B, "NDVI", 0.5, 0.79) == so.SKIP
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.85) == so.SKIP  # not the reference index
    assert ow._write_scene(PID_B, "NDMI", 0.45, 0.85) == so.SKIP
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_A
    values = _vals(store)
    assert {"ndvi", "ndre", "ndmi"} <= set(values) and values["ndvi"] == "0.600000"


def test_i1_reference_index_arriving_last_replaces_and_logs_what_it_drops(store, caplog):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.95) == so.SKIP
    with caplog.at_level(logging.INFO):
        assert ow._write_scene(PID_B, "NDVI", 0.7, 0.90) == so.REPLACE
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_B
    values = _vals(store)
    assert values["ndvi"] == "0.700000" and "ndre" not in values  # nothing of A survives (B's NDRE: see the jobs tests)
    assert any("ndre" in r.getMessage() and "dropped" in r.getMessage() for r in caplog.records)


def test_i1_better_scene_ends_entirely_from_b(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.70)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.70)
    assert ow._write_scene(PID_B, "NDVI", 0.7, 0.90) == so.REPLACE
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.91) == so.MERGE
    entity = store.entities[PARCEL_ID]
    values = _vals(store)
    assert entity["productID"]["value"] == PID_B
    assert values["ndvi"] == "0.700000" and values["ndre"] == "0.550000"


def test_i1_stored_without_fraction_keeps_today_semantics_for_reference_only(store):
    ow._write_scene(PID_A, "NDVI", 0.6)
    ow._write_scene(PID_A, "NDRE", 0.5)
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.9) == so.SKIP
    assert ow._write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE  # nothing to defend on the reference index
    assert store.entities[PARCEL_ID]["productID"]["value"] == PID_B


def test_i1_scene_over_statistical_api_only_through_reference_index(store):
    day = ow.DAY
    for idx, mean in (("NDVI", 0.6), ("NDRE", 0.5)):
        analytics = so.index_analytics(idx, {**ow.STATS, "mean": mean})
        analytics["pixelCount"] = 40
        so.write_optical(TENANT, so.parcel_target(ow.PARCEL, ow.GEOM), so.no_scene_take(day), analytics,
                         index_type=idx)
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.9) == so.SKIP
    assert store.entities[PARCEL_ID].get("productID") is None
    assert ow._write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE


# ── I2 / I4: the broker HTTP layer, faked underneath the SDK ────────────────

class _Resp:
    def __init__(self, status=204, body=None):
        self.status_code = status
        self._body = body
        self.content = b"x" if body is not None else b""
        self.text = ""
        self.request = None

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeHttp:
    """Replaces requests.Session verbs: a script of responses/exceptions per verb."""

    def __init__(self, monkeypatch, post=(), get=()):
        import requests
        self.scripts = {"post": list(post), "get": list(get)}
        self.calls = {"post": [], "get": []}
        for verb in ("post", "get"):
            monkeypatch.setattr(requests.Session, verb, self._make(verb))
        self.sleeps = []
        monkeypatch.setattr("time.sleep", lambda s: self.sleeps.append(s))

    def _make(self, verb):
        def call(session, url, **kw):
            self.calls[verb].append((url, kw))
            item = self.scripts[verb].pop(0) if self.scripts[verb] else _Resp(204)
            if isinstance(item, BaseException):
                raise item
            return item
        return call


ENTITY = {"id": "urn:ngsi-ld:EOSatelliteImagery:acme:parcel:p:s2-indices:2026-06-21", "type": "EOSatelliteImagery"}


def test_i2_upsert_works_inside_a_running_event_loop(monkeypatch):
    import asyncio
    http = FakeHttp(monkeypatch, post=[_Resp(204)])

    async def inside():
        return fi.upsert_eo_satellite_imagery("acme", dict(ENTITY))

    assert asyncio.run(inside()) == ENTITY["id"]
    url, kw = http.calls["post"][0]
    assert url.endswith("/ngsi-ld/v1/entityOperations/upsert") and kw["params"] == {"options": "update"}
    assert kw["json"][0]["@context"] and kw["headers"]["NGSILD-Tenant"] == "acme"


def test_i2_upsert_207_with_errors_is_failure_and_logged(monkeypatch, caplog):
    FakeHttp(monkeypatch, post=[_Resp(207, {"success": [], "errors": [{"entityId": "x"}]})])
    with caplog.at_level(logging.ERROR):
        assert fi.upsert_eo_satellite_imagery("acme", dict(ENTITY)) is None
    assert any("upsert" in r.getMessage() for r in caplog.records)


def test_i2_remove_job_index_from_a_coroutine_deletes_through_the_broker(monkeypatch):
    import asyncio
    from datetime import date
    from app.services.eo_satellite_imagery import Family
    store = ow.Store()
    monkeypatch.setattr(fi, "get_eo_satellite_imagery", store.get)
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", store.upsert)
    monkeypatch.setattr(fi, "delete_eo_attribute", store.delete_attr)
    monkeypatch.setattr(fi, "delete_eo_satellite_imagery", store.delete)
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)
    ow._write_scene(PID_A, "NDVI", 0.6, 0.8)
    ow._write_scene(PID_A, "EVI", 0.4, 0.8)
    http = FakeHttp(monkeypatch, post=[_Resp(204)])
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", _REAL_UPSERT)

    async def inside():
        return so.remove_index(TENANT, "parcel", ow.PARCEL, Family.S2_INDICES, ow.DAY, "ndvi")

    assert asyncio.run(inside()) is True
    assert len(http.calls["post"]) == 1  # the pruned entity reached the broker


# ── I4 retry ────────────────────────────────────────────────────────────────

def test_i4_upsert_retries_connection_errors_and_5xx_then_succeeds(monkeypatch):
    import requests
    http = FakeHttp(monkeypatch, post=[requests.ConnectionError("down"), _Resp(503), _Resp(204)])
    assert fi.upsert_eo_satellite_imagery("acme", dict(ENTITY)) == ENTITY["id"]
    assert len(http.calls["post"]) == 3 and sum(http.sleeps) < 10


def test_i4_upsert_gives_up_after_three_attempts(monkeypatch):
    import requests
    http = FakeHttp(monkeypatch, post=[requests.Timeout("t")] * 5)
    assert fi.upsert_eo_satellite_imagery("acme", dict(ENTITY)) is None
    assert len(http.calls["post"]) == 3 and sum(http.sleeps) < 10


def test_i4_upsert_never_retries_4xx(monkeypatch):
    http = FakeHttp(monkeypatch, post=[_Resp(400), _Resp(204)])
    assert fi.upsert_eo_satellite_imagery("acme", dict(ENTITY)) is None
    assert len(http.calls["post"]) == 1 and http.sleeps == []


def test_i4_get_retries_then_returns_entity_and_404_is_absent(monkeypatch):
    import requests
    http = FakeHttp(monkeypatch, get=[_Resp(502), requests.ConnectionError("x"), _Resp(200, {"id": "e"})])
    assert fi.get_eo_satellite_imagery("acme", "e") == {"id": "e"}
    assert len(http.calls["get"]) == 3
    http = FakeHttp(monkeypatch, get=[_Resp(404), _Resp(200, {"id": "e"})])
    assert fi.get_eo_satellite_imagery("acme", "e") is None
    assert len(http.calls["get"]) == 1


def test_i4_get_raises_after_three_failures_and_never_retries_4xx(monkeypatch):
    http = FakeHttp(monkeypatch, get=[_Resp(500)] * 3)
    with pytest.raises(Exception):
        fi.get_eo_satellite_imagery("acme", "e")
    assert len(http.calls["get"]) == 3
    http = FakeHttp(monkeypatch, get=[_Resp(403), _Resp(200, {})])
    with pytest.raises(Exception):
        fi.get_eo_satellite_imagery("acme", "e")
    assert len(http.calls["get"]) == 1


# ── I3 deletes must not remove another live job's value ─────────────────────

from types import SimpleNamespace  # noqa: E402
import uuid  # noqa: E402
from app.services import zone_products as zp  # noqa: E402
from app.models import VegetationJob, VegetationScene  # noqa: E402


def _job(index="NDVI", tenant=TENANT, status="completed", deleted=None, result_extra=None, entity=ow.PARCEL):
    result = {"index_type": index, "sensing_date": ow.DAY.isoformat(), "is_composite": False,
              "scene_id": str(ow.SCENE_UUID), **(result_extra or {})}
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant, entity_id=entity, status=status, deleted_at=deleted,
                           result=result, parameters={"index_type": index}, job_type="calculate_index",
                           crop_season_id=None, celery_task_id=None, deleted=None)


class _Savepoints:
    """Session double: records every savepoint and whether an error went through it."""

    savepoints = 0
    rolled_back = 0

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


class FakeDb(_Savepoints):
    """first() walks ``targets`` (the jobs being deleted); all() answers the owners query with ``others``."""

    def __init__(self, targets, others):
        self.targets, self.others, self._i = list(targets), list(others), 0
        self.queries = 0

    def query(self, model):
        db = self
        if model is VegetationScene:
            return _Q(lambda: SimpleNamespace(scene_id=PID_A), lambda: [])
        db.queries += 1

        def first():
            row = db.targets[min(db._i, len(db.targets) - 1)]
            db._i += 1
            return row
        return _Q(first, lambda: list(db.others))

    def commit(self):
        pass


class _Q:
    def __init__(self, first, all_):
        self._first, self._all = first, all_

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return self._first()

    def all(self):
        return self._all()


def _seed_entity():
    ow._write_scene(PID_A, "NDVI", 0.6, 0.8)
    ow._write_scene(PID_A, "EVI", 0.4, 0.8)


def _bulk(db, jobs):
    import asyncio
    from app.api import jobs as jobs_api
    zone = {"zone_id": "z", "zone_urn": ow.ZONE, "relation": zp.CURRENT_ZONE_RELATION}
    from unittest.mock import patch
    with patch.object(fi, "delete_eo_index"), patch.object(zp, "fetch_zone_targets", return_value=[zone]):
        return asyncio.run(jobs_api.bulk_delete_jobs(
            jobs_api.BulkDeleteRequest(ids=[str(j.id) for j in jobs]), current_user={"tenant_id": TENANT}, db=db))


def _ndvi_present(store):
    return "ndvi" in _vals(store)


def test_i3_skipped_job_does_not_remove_the_winners_value(store):
    _seed_entity()
    for extra in ({"skipped": True}, {"skip_reason": "no_valid_pixels"}):
        job = _job(result_extra=extra)
        assert so.remove_job_index(TENANT, ow.PARCEL, {**job.result, "scene_product_id": PID_A}, "NDVI") is True
        assert _ndvi_present(store), extra
    reads = store.reads
    so.remove_job_index(TENANT, ow.PARCEL, {**_job(result_extra={"skipped": True}).result}, "NDVI")
    assert store.reads == reads  # decided before touching the broker


def test_i3_zone_deletion_of_a_skipped_job_keeps_the_zone_value(store):
    _seed_entity()
    zone_eid = f"urn:ngsi-ld:EOSatelliteImagery:{TENANT}:zone:parcel-1:run-1:L1:s2-indices:2026-06-21"
    za = so.index_analytics("NDVI", ow.STATS)
    za["pixelCount"] = 40
    so.write_optical(TENANT, so.zone_target(ow.PARCEL, ow.ZONE, ow.GEOM), so.scene_take(ow._scene(pid=PID_A)), za,
                     index_type="NDVI", follow=so.CREATE)
    zone = {"zone_id": "z", "zone_urn": ow.ZONE, "relation": zp.CURRENT_ZONE_RELATION}
    from unittest.mock import patch
    result = {**_job(result_extra={"skip_reason": "no_valid_pixels"}).result, "scene_product_id": PID_A}
    with patch.object(fi, "delete_eo_index"), patch.object(zp, "fetch_zone_targets", return_value=[zone]):
        zp.delete_zone_products(TENANT, ow.PARCEL, ow.DAY.isoformat(), "NDVI", job_result=result)
    assert zone_eid in store.entities
    with patch.object(fi, "delete_eo_index"), patch.object(zp, "fetch_zone_targets", return_value=[zone]):
        zp.delete_zone_products(TENANT, ow.PARCEL, ow.DAY.isoformat(), "NDVI",
                                job_result={**result, "skip_reason": None}, keep_if=lambda: True)
    assert zone_eid in store.entities  # another live job owns it
    with patch.object(fi, "delete_eo_index"), patch.object(zp, "fetch_zone_targets", return_value=[zone]):
        zp.delete_zone_products(TENANT, ow.PARCEL, ow.DAY.isoformat(), "NDVI",
                                job_result={**result, "skip_reason": None}, keep_if=lambda: False)
    assert zone_eid not in store.entities


def test_i3_bulk_delete_keeps_the_value_while_another_live_job_owns_it(store):
    _seed_entity()
    target, other = _job(), _job()
    _bulk(FakeDb([target], [other]), [target])
    assert _ndvi_present(store)


def test_i3_jobs_deleted_together_do_not_protect_each_other(store):
    _seed_entity()
    a, b = _job(), _job()
    _bulk(FakeDb([a, b], [a, b]), [a, b])
    assert not _ndvi_present(store) and "evi" in _vals(store)


@pytest.mark.parametrize("kw", [{"tenant": "other"}, {"status": "failed"}, {"deleted": "yes"},
                                {"result_extra": {"skipped": True}}, {"result_extra": {"sensing_date": "2026-06-20"}},
                                {"result_extra": {"scene_id": "other-scene"}}, {"index": "EVI"},
                                {"entity": "urn:ngsi-ld:AgriParcel:parcel-2"}])
def test_i3_rows_that_do_not_own_the_same_value_do_not_protect(store, kw):
    _seed_entity()
    target = _job()
    kw = dict(kw)
    deleted = kw.pop("deleted", None)
    other = _job(**kw)
    if deleted:
        from datetime import datetime, timezone
        other.deleted_at = datetime.now(timezone.utc)
    _bulk(FakeDb([target], [other]), [target])
    assert not _ndvi_present(store)


def test_i3_period_delete_honours_the_guard(store):
    import asyncio
    from app.api import monitoring_periods as mp
    from app.models.crop_seasons import VegetationMonitoringPeriod
    from unittest.mock import MagicMock, patch
    _seed_entity()
    target, other = _job(), _job()
    zone = {"zone_id": "z", "zone_urn": ow.ZONE, "relation": zp.CURRENT_ZONE_RELATION}

    class Db(FakeDb):
        def query(self, model):
            if model is VegetationMonitoringPeriod:
                return _Q(lambda: MagicMock(), lambda: [])
            if model is VegetationJob:
                self.queries += 1
                # the cascade lists the period's jobs with .all(); the guard asks .all() too: tell them apart
                return _Q(lambda: target, lambda: [target] if self.queries == 1 else [other])
            return super().query(model)

    with patch.object(fi, "delete_eo_index"), patch.object(zp, "fetch_zone_targets", return_value=[zone]), \
            patch("app.services.fiware_integration.delete_eo_product"):
        asyncio.run(mp.delete_monitoring_period(ow.PARCEL, str(uuid.uuid4()), cascade=True,
                                                current_user={"tenant_id": TENANT}, db=Db([target], [other])))
    assert _ndvi_present(store)


def test_i3_owner_query_compiles_for_postgres():
    from sqlalchemy.dialects import postgresql
    seen = []

    class Db(_Savepoints):
        def query(self, model):
            class Q:
                def filter(self, *clauses):
                    seen.extend(str(c.compile(dialect=postgresql.dialect())) for c in clauses)
                    return self

                def all(self):
                    return []
            return Q()

    target = _job()
    assert so.other_job_holds_value(Db(), TENANT, target, target.result, "NDVI") is False
    text = " ".join(seen)
    assert "tenant_id" in text and "deleted_at IS NULL" in text and "status" in text


# ── baseline: never leave old interior values next to a new mean ────────────

def _baseline(index, mean):
    from app.tasks import historical_baseline as hb
    scene = {"id": PID_A, "datetime": "2026-06-21T11:06:21Z", "geometry": ow.FOOT, "cloud_cover": 10.0}
    hb._write_satimg(TENANT, ow.PARCEL, ow.GEOM, index, {**ow.STATS, "mean": mean}, scene, ow.DAY)


def test_baseline_over_the_same_scene_keeps_the_stored_index_values(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.8)
    before = dict(_vals(store))
    _baseline("NDVI", 0.55)
    values = _vals(store)
    assert values["ndvi"] == "0.600000" and values["ndvi.interiorMean"] == before["ndvi.interiorMean"]
    assert values["ndvi.interiorValidFraction"] == "0.8000"
    _baseline("EVI", 0.35)  # an index the entity lacks is added whole
    values = _vals(store)
    assert values["evi"] == "0.350000" and values["ndvi"] == "0.600000"


# ── radar winner: validPixelFraction, then pixelCount; zones follow the parcel ──

from app.services import eo_satimg_radar_lst as rl  # noqa: E402


def _bs(count, frac):
    out = {"mean": -10.0, "pixel_count": count}
    if frac is not None:
        out["valid_fraction"] = frac
    return {"VV": out}


@pytest.fixture
def rstore(monkeypatch):
    s = sl.Store()
    monkeypatch.setattr(fi, "get_eo_satellite_imagery", s.get)
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", s.upsert)
    monkeypatch.setattr(fi, "delete_eo_satellite_imagery", s.delete)
    monkeypatch.setattr(fi, "delete_eo_attribute", s.delete_attr)
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)
    return s


def _takes():
    a = rl.radar_take(sl.S1_A, sl.GRD_PARAMS)
    b = rl.radar_take(sl.S1_B, {**sl.GRD_PARAMS, "scene_id": sl.S1_B, "scene_datetime": "2026-06-21T17:20:01Z"})
    return a, b, rl.parcel_target(sl.PARCEL, sl.GEOM)


def test_radar_fraction_decides_when_both_takes_have_it(rstore):
    a, b, target = _takes()
    assert rl.write_backscatter(TENANT, target, a, _bs(40, 0.80)) == rl.CREATE
    assert rl.write_backscatter(TENANT, target, b, _bs(90, 0.70)) == rl.SKIP   # more pixels, less valid
    assert rl.write_backscatter(TENANT, target, b, _bs(30, 0.90)) == rl.REPLACE  # fewer pixels, more valid
    assert sl._prop(rstore.entities[sl.BS_ID], "productID") == sl.S1_B
    assert rl.write_backscatter(TENANT, target, a, _bs(30, 0.90)) == rl.SKIP   # tie keeps the stored one


def test_radar_falls_back_to_count_when_a_take_lacks_the_fraction(rstore):
    a, b, target = _takes()
    assert rl.write_backscatter(TENANT, target, a, _bs(40, 0.80)) == rl.CREATE
    assert rl.write_backscatter(TENANT, target, b, _bs(30, None)) == rl.SKIP
    assert rl.write_backscatter(TENANT, target, b, _bs(50, None)) == rl.REPLACE
    assert rl.write_backscatter(TENANT, target, a, _bs(50, 0.1)) == rl.SKIP  # tie on count, stored lacks the fraction


def test_radar_soil_moisture_keeps_deciding_by_its_count(rstore):
    a, b, target = _takes()
    smi = lambda n: {"mean": 0.3, "min": 0.1, "max": 0.6, "std": 0.05, "pixel_count": n}  # noqa: E731
    assert rl.write_soil_moisture(TENANT, target, a, smi(40)) == rl.CREATE
    assert rl.write_soil_moisture(TENANT, target, b, smi(40)) == rl.SKIP
    assert rl.write_soil_moisture(TENANT, target, b, smi(41)) == rl.REPLACE


ZONE_ID = sl.ZN_ID


def test_radar_zone_is_skipped_when_the_parcel_take_lost(rstore):
    a, b, target = _takes()
    rl.write_backscatter(TENANT, target, b, {"VV": {"mean": -9.0, "pixel_count": 500, "valid_fraction": 0.99},
                                              "VH": {"mean": -9.0, "pixel_count": 500, "valid_fraction": 0.99}})
    sl.run_s1(zones=[sl.CURRENT_ZONE])
    assert sl._prop(rstore.entities[sl.BS_ID], "productID") == sl.S1_B  # the parcel verdict was skip
    assert ZONE_ID not in rstore.entities


def test_radar_zone_follows_a_winning_parcel_and_is_written_after_it(rstore):
    a, b, target = _takes()
    zt = rl.zone_target(sl.PARCEL, sl.ZONE, sl.GEOM)
    big = {"VV": {"mean": -9.0, "pixel_count": 500, "valid_fraction": 0.99},
           "VH": {"mean": -9.0, "pixel_count": 500, "valid_fraction": 0.99}}
    rl.write_backscatter(TENANT, zt, b, big)  # the zone holds another scene with more coverage
    rstore.upserts.clear()
    sl.run_s1(zones=[sl.CURRENT_ZONE])
    ids = [u["id"] for u in rstore.upserts]
    assert ids.index(sl.BS_ID) < ids.index(ZONE_ID)
    assert sl._prop(rstore.entities[ZONE_ID], "productID") == sl.S1_A  # follows the parcel, not its own count


# ── LST: larger coverage, then the most recent ──────────────────────────────

def _lst_stats(n):
    return {"mean": 30.0, "min": 29.0, "max": 31.0, "std": 0.5, "pixel_count": n}


def _clms(hour, suffix=None, date_only=False):
    when = "2026-06-20" if date_only else f"2026-06-20T{hour:02d}:00:00Z"
    return rl.clms_take({"id": f"{sl.CLMS_ID}-{suffix or hour}", "geometry": sl.FOOT, "properties": {"datetime": when}})


def _cl(store):
    return store.entities[sl.CL_ID]


def test_clms_equal_coverage_latest_hour_wins_in_both_arrival_orders(rstore):
    target = rl.parcel_target(sl.PARCEL, sl.GEOM)
    assert rl.write_lst(TENANT, target, _clms(11), _lst_stats(1)) == rl.CREATE
    assert rl.write_lst(TENANT, target, _clms(12), _lst_stats(1)) == rl.REPLACE
    assert sl._prop(_cl(rstore), "sensingDate") == "2026-06-20T12:00:00Z"
    assert sl._prop(_cl(rstore), "productID").endswith("-12")
    rstore.entities.clear()
    assert rl.write_lst(TENANT, target, _clms(12), _lst_stats(1)) == rl.CREATE
    assert rl.write_lst(TENANT, target, _clms(11), _lst_stats(1)) == rl.SKIP
    assert sl._prop(_cl(rstore), "sensingDate") == "2026-06-20T12:00:00Z"
    assert sl._prop(_cl(rstore), "productID").endswith("-12")


def test_lst_larger_coverage_beats_a_later_hour(rstore):
    target = rl.parcel_target(sl.PARCEL, sl.GEOM)
    rl.write_lst(TENANT, target, _clms(12), _lst_stats(1))
    assert rl.write_lst(TENANT, target, _clms(9), _lst_stats(2)) == rl.REPLACE
    assert sl._prop(_cl(rstore), "sensingDate") == "2026-06-20T09:00:00Z"
    assert rl.write_lst(TENANT, target, _clms(15), _lst_stats(1)) == rl.SKIP


def test_lst_tie_without_time_never_displaces_one_with_time(rstore):
    target = rl.parcel_target(sl.PARCEL, sl.GEOM)
    rl.write_lst(TENANT, target, _clms(11), _lst_stats(1))
    assert rl.write_lst(TENANT, target, _clms(0, "notime", date_only=True), _lst_stats(1)) == rl.SKIP
    assert sl._prop(_cl(rstore), "sensingDate") == "2026-06-20T11:00:00Z"
    rstore.entities.clear()
    rl.write_lst(TENANT, target, _clms(0, "notime", date_only=True), _lst_stats(1))
    assert sl._prop(_cl(rstore), "sensingDate") == "2026-06-20"
    assert rl.write_lst(TENANT, target, _clms(0, "other", date_only=True), _lst_stats(1)) == rl.SKIP  # both untimed
    assert rl.write_lst(TENANT, target, _clms(8), _lst_stats(1)) == rl.REPLACE  # a time over none


def test_landsat_lst_uses_the_same_rule(rstore):
    target = rl.parcel_target(sl.PARCEL, sl.GEOM)
    item = lambda pid, t: rl.landsat_take({"id": pid, "geometry": sl.FOOT,  # noqa: E731
                                           "properties": {"datetime": f"2026-06-20T{t}Z", "platform": "landsat-9"}})
    assert rl.write_lst(TENANT, target, item(sl.LANDSAT_ID, "10:50:12"), _lst_stats(4)) == rl.CREATE
    assert rl.write_lst(TENANT, target, item("LC08_L2SP_200031_20260620_20260621_02_T1_ST", "10:51:00"),
                        _lst_stats(4)) == rl.REPLACE
    assert rl.write_lst(TENANT, target, item(sl.LANDSAT_ID, "10:50:12"), _lst_stats(4)) == rl.SKIP


def test_radar_tie_still_keeps_the_stored_take(rstore):
    a, b, target = _takes()  # b is the later pass
    rl.write_backscatter(TENANT, target, a, _bs(40, 0.8))
    assert rl.write_backscatter(TENANT, target, b, _bs(40, 0.8)) == rl.SKIP


def test_fix1_better_scene_job_is_decided_by_the_take_not_by_its_skip_reason(store):
    _seed_entity()  # the entity is scene A's take
    own = {**_job(result_extra={"skip_reason": "better_scene_same_date"}).result, "scene_product_id": PID_A}
    other_scene = {**own, "scene_product_id": PID_B}
    assert so.remove_job_index(TENANT, ow.PARCEL, other_scene, "NDVI") is True
    assert _ndvi_present(store)  # the entity holds another scene's take: untouched
    assert so.remove_job_index(TENANT, ow.PARCEL, own, "NDVI") is True
    assert not _ndvi_present(store)  # it holds this job's scene: its values go


def test_fix1_better_scene_job_protects_the_value_it_owns():
    holder = _job(result_extra={"skip_reason": "better_scene_same_date"})
    target = _job()
    assert so.other_job_holds_value(FakeDb([target], [holder]), TENANT, target, target.result, "NDVI") is True
    skipped = _job(result_extra={"skip_reason": "no_valid_pixels"})
    assert so.other_job_holds_value(FakeDb([target], [skipped]), TENANT, target, target.result, "NDVI") is False


# ── fix round 1: complete the take from the other completed jobs of the scene ──

from datetime import datetime, timedelta, timezone  # noqa: E402
import app.tasks.processing_tasks as pt  # noqa: E402

B_UUID = ow.SCENE_UUID
_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def _stats(mean):
    return {"mean": mean, "min": mean - 0.2, "max": mean + 0.2, "std": 0.1, "pixel_count": 40}


def _interior(frac, mean):
    return {"valid_fraction": frac, "valid_pixels": 30, "mask": "core", "mean": mean, "median": mean,
            "p10": mean - 0.1, "p90": mean + 0.1}


def _idx_job(index, mean, frac=0.9, *, scene=B_UUID, tenant=TENANT, status="completed", deleted=None,
             minutes=0, extra=None, entity=ow.PARCEL, composite=False):
    result = {"index_type": index, "sensing_date": ow.DAY.isoformat(), "is_composite": composite,
              "scene_id": str(scene), "statistics": _stats(mean), "interior": _interior(frac, mean), **(extra or {})}
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant, entity_id=entity, status=status, deleted_at=deleted,
                           result=result, parameters={"index_type": index}, job_type="calculate_index",
                           completed_at=_T0 + timedelta(minutes=minutes))


class JobsDb(_Savepoints):
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.evaluated = list(rows), fail, 0

    def query(self, model):
        self.evaluated += 1
        if self.fail:
            raise RuntimeError("db down")
        return _Q(lambda: None, lambda: list(self.rows))


def _publish(db, index, mean, frac, *, pid=PID_B, scene_uuid=B_UUID):
    scene = ow._scene(pid=pid, scene_uuid=scene_uuid)
    job = SimpleNamespace(id=uuid.uuid4(), entity_id=ow.PARCEL)
    stats = _stats(mean)
    folder = f"{TENANT}/entities/{ow.PARCEL}/scenes/{scene_uuid}/{index.lower()}.tif"
    return pt._publish_satimg_parcel(
        TENANT, job, index, stats, scene, window=None, raster_url=f"s3://bucket/{folder}",
        interior=_interior(frac, mean), parcel_cloud_fraction=None, geometry=ow.GEOM, lost_day=False, db=db)


def _means(store):
    v = _vals(store)
    return {n: v[n] for n in v if n in ("ndvi", "ndre", "ndmi", "evi")}


def test_fix1_reference_arriving_last_ends_with_all_of_bs_indices_and_none_of_as(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    ow._write_scene(PID_A, "NDMI", 0.4, 0.84)
    db = JobsDb([_idx_job("NDRE", 0.55, 0.85), _idx_job("NDMI", 0.45, 0.85)])
    assert _publish(db, "NDRE", 0.55, 0.85) is None  # skip (not the reference index)
    zone = _publish(db, "NDVI", 0.7, 0.90)
    assert zone is not None and zone.parcel_verdict == so.REPLACE
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_B
    assert _means(store) == {"ndvi": "0.700000", "ndre": "0.550000", "ndmi": "0.450000"}
    assert _vals(store)["ndre.interiorValidFraction"] == "0.8500"


def test_fix1_failed_previous_write_is_healed_by_the_next_index(store):
    ow._write_scene(PID_B, "NDVI", 0.7, 0.9)  # B's NDRE write failed earlier: only NDVI is stored
    db = JobsDb([_idx_job("NDRE", 0.55, 0.85), _idx_job("NDVI", 0.7, 0.9)])
    assert _publish(db, "EVI", 0.35, 0.9).parcel_verdict == so.MERGE
    assert _means(store) == {"ndvi": "0.700000", "ndre": "0.550000", "evi": "0.350000"}


def test_fix1_index_added_to_an_old_scene_without_entity_creates_it_whole(store):
    db = JobsDb([_idx_job("NDVI", 0.7), _idx_job("NDRE", 0.55)])
    assert _publish(db, "EVI", 0.35, 0.9).parcel_verdict == so.CREATE
    assert _means(store) == {"ndvi": "0.700000", "ndre": "0.550000", "evi": "0.350000"}


@pytest.mark.parametrize("row", [
    dict(deleted=_T0), dict(status="failed"), dict(tenant="other"), dict(scene=uuid.uuid4()),
    dict(entity="urn:ngsi-ld:AgriParcel:parcel-2"), dict(extra={"skipped": True}),
    dict(extra={"skip_reason": "no_valid_pixels"}), dict(composite=True), dict(extra={"is_custom": True}),
    dict(index="CUSTOM"), dict(index="VRA_ZONES"),
])
def test_fix1_rows_that_are_not_this_scenes_completed_jobs_are_not_used(store, row):
    row = dict(row)
    index = row.pop("index", "NDRE")
    db = JobsDb([_idx_job(index, 0.55, **row)])
    _publish(db, "NDVI", 0.7, 0.9)
    assert _means(store) == {"ndvi": "0.700000"}


def test_fix1_better_scene_job_counts_and_latest_of_an_index_wins(store):
    db = JobsDb([_idx_job("NDRE", 0.50, minutes=0), _idx_job("NDRE", 0.55, minutes=5),
                 _idx_job("NDMI", 0.45, extra={"skip_reason": "better_scene_same_date"})])
    _publish(db, "NDVI", 0.7, 0.9)
    assert _means(store) == {"ndvi": "0.700000", "ndre": "0.550000", "ndmi": "0.450000"}


def test_fix1_take_wide_values_do_not_come_from_jobs(store):
    row = _idx_job("NDRE", 0.55)
    row.result["statistics"]["pixel_count"] = 999
    row.result["interior"]["valid_fraction"] = 0.99
    _publish(JobsDb([row]), "NDVI", 0.7, 0.9)
    v = _vals(store)
    assert v["pixelCount"] == "40" and v["ndre.interiorValidFraction"] == "0.9900"
    assert "validPixelFraction" not in v and "parcelCloudFraction" not in v


def test_fix1_stored_value_is_never_overwritten_by_a_job(store):
    ow._write_scene(PID_B, "NDRE", 0.52, 0.8)  # same take, stored by an earlier write
    _publish(JobsDb([_idx_job("NDRE", 0.55, 0.9)]), "NDVI", 0.7, 0.9)
    v = _vals(store)
    assert v["ndre"] == "0.520000" and v["ndre.interiorValidFraction"] == "0.8000"


def test_fix1_complete_is_not_evaluated_on_skip_or_switch_off(store, monkeypatch):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.9)
    db = JobsDb([_idx_job("NDRE", 0.55)])
    assert _publish(db, "NDVI", 0.5, 0.5) is None  # worse reference: skip
    assert _publish(db, "NDRE", 0.5, 0.99) is None  # non-reference: skip
    assert db.evaluated == 0
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", "")
    _publish(db, "NDVI", 0.9, 0.99)
    assert db.evaluated == 0


def test_fix1_query_failure_warns_and_writes_without_it(store, caplog):
    with caplog.at_level(logging.WARNING):
        zone = _publish(JobsDb([], fail=True), "NDVI", 0.7, 0.9)
    assert zone.parcel_verdict == so.CREATE and _means(store) == {"ndvi": "0.700000"}
    assert any("not completed from the jobs" in r.getMessage() for r in caplog.records)


def test_fix1_composite_is_left_as_it_was(store):
    db = JobsDb([_idx_job("NDRE", 0.55)])
    job = SimpleNamespace(id=uuid.uuid4(), entity_id=ow.PARCEL)
    scene = ow._scene(pid=PID_B, scene_uuid=B_UUID)
    pt._publish_satimg_parcel(TENANT, job, "NDVI", _stats(0.7), scene, window=("2026-06-01", "2026-06-21"),
                              raster_url="s3://bucket/x/composites/w/ndvi.tif", interior=None,
                              parcel_cloud_fraction=None, geometry=ow.GEOM, lost_day=False, db=db)
    assert db.evaluated == 0


def test_fix1_owner_query_sql_is_compilable(store):
    from sqlalchemy.dialects import postgresql
    seen = []

    class Db(_Savepoints):
        def query(self, model):
            class Q:
                def filter(self, *c):
                    seen.extend(str(x.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
                                for x in c)
                    return self

                def order_by(self, *c):
                    return self

                def all(self):
                    return []
            return Q()

    so.completing_analytics(Db(), TENANT, SimpleNamespace(id=uuid.uuid4(), entity_id=ow.PARCEL), B_UUID, "NDVI")
    text = " ".join(seen)
    assert "tenant_id" in text and "completed" in text and "deleted_at IS NULL" in text and str(B_UUID) in text


# ── fix round 1, item 3: what the reader returns for the final entities ──────

def _legacy_entity():
    from unittest.mock import patch
    sent = []
    with patch.object(fi, "_upsert_eoproduct_entity", side_effect=lambda t, e: sent.append(e) or {"upserted": 1}):
        fi.upsert_eo_index(TENANT, ow.PARCEL, "NDVI", {**ow.STATS, "mean": 0.58}, ow.DAY,
                           raster_url=f"s3://bucket/{TENANT}/entities/{ow.PARCEL}/scenes/{B_UUID}/ndvi.tif")
    assert sent and sent[0]["type"] == "EOProduct"
    return sent[0]


def _read_day(store):
    from nkz_platform_sdk import parse_eo_entities
    found = parse_eo_entities([_legacy_entity(), store.entities[PARCEL_ID]], require={"ndvi"})
    assert len(found) == 1 and found[0].sensing_day == ow.DAY
    return found[0]


def test_fix1_reader_returns_the_day_when_a_losing_scene_was_skipped(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    ow._write_scene(PID_B, "NDVI", 0.5, 0.79)
    ow._write_scene(PID_B, "NDRE", 0.55, 0.85)
    obs = _read_day(store)
    assert obs.product_id == PID_A and obs.values["ndvi"] == 0.6


def test_fix1_reader_returns_the_day_when_the_reference_index_arrives_last(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    db = JobsDb([_idx_job("NDRE", 0.55, 0.85)])
    _publish(db, "NDRE", 0.55, 0.85)
    _publish(db, "NDVI", 0.7, 0.90)
    obs = _read_day(store)
    assert obs.product_id == PID_B and obs.values["ndvi"] == 0.7 and obs.values["ndre"] == 0.55


# ── controller: a failed query must not poison the job's session; no index is half filled ──

def test_fix2_failed_completion_query_is_rolled_back_to_its_savepoint(store):
    db = JobsDb([], fail=True)
    _publish(db, "NDVI", 0.7, 0.9)
    assert (db.savepoints, db.rolled_back) == (1, 1)
    assert _means(store) == {"ndvi": "0.700000"}


def test_fix2_failed_owner_and_scene_queries_are_rolled_back_to_their_savepoints():
    target = _job()
    db = JobsDb([], fail=True)
    assert so.other_job_holds_value(db, TENANT, target, target.result, "NDVI") is True
    so.with_scene_product(db, TENANT, {"scene_id": str(B_UUID)})
    assert (db.savepoints, db.rolled_back) == (2, 2)


def test_fix2_jobs_never_fill_part_of_an_index_that_is_already_stored(store):
    so.write_optical(TENANT, so.parcel_target(ow.PARCEL, ow.GEOM), so.scene_take(ow._scene(pid=PID_B, scene_uuid=B_UUID)),
                     {"ndvi": 0.7, "pixelCount": 40}, index_type="NDVI")  # no interior: deliberately without it
    _publish(JobsDb([_idx_job("NDVI", 0.1), _idx_job("NDRE", 0.55)]), "EVI", 0.35, 0.9)
    values = _vals(store)
    assert not [n for n in values if n.startswith("ndvi.")]
    assert _means(store) == {"ndvi": "0.700000", "ndre": "0.550000", "evi": "0.350000"}
    assert values["ndre.interiorValidFraction"] == "0.9000"
