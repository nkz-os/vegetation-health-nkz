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


def test_i1_reference_index_arriving_last_replaces_whole_entity(store, caplog):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.95) == so.SKIP
    with caplog.at_level(logging.INFO):
        assert ow._write_scene(PID_B, "NDVI", 0.7, 0.90) == so.REPLACE
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_B
    values = _vals(store)
    assert values["ndvi"] == "0.700000" and "ndre" not in values  # nothing of A survives
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


class FakeDb:
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
    for extra in ({"skipped": True}, {"skip_reason": "better_scene_same_date"}, {"skip_reason": "no_valid_pixels"}):
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
    result = {**_job(result_extra={"skip_reason": "better_scene_same_date"}).result, "scene_product_id": PID_A}
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

    class Db:
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
