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
