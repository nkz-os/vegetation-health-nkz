"""Zone publication: meanValue, zoningRunId and removal of stale zones."""
import logging
from unittest.mock import MagicMock

import requests

from app.jobs.zoning_algorithm import ZoningAlgorithm
from app.services.fiware_integration import FIWAREClient

PARCEL = "urn:ngsi-ld:AgriParcel:acme:p1"
PREFIX = "urn:ngsi-ld:AgriManagementZone:p1:Z"


def _vectors(n):
    return [
        {
            "properties": {
                "zone_id": k, "cluster_id": k, "zone_class": "medium",
                "mean_value": round(0.2 + 0.1 * k, 4), "area_ha": 1.5,
                "prescription_rate": 1.0,
            },
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        }
        for k in range(n)
    ]


def _algo(existing_ids=(), delete_side_effect=None):
    algo = ZoningAlgorithm.__new__(ZoningAlgorithm)
    algo.tenant_id = "acme"
    algo.fiware = MagicMock()
    algo.fiware.create_entity.return_value = True
    algo.fiware.list_entity_ids.return_value = list(existing_ids)
    if delete_side_effect is not None:
        algo.fiware.delete_entity.side_effect = delete_side_effect
    return algo


def _sent(algo):
    return [c.args[0] for c in algo.fiware.create_entity.call_args_list]


def test_entities_carry_mean_value_and_shared_run_id():
    algo = _algo()
    vectors = _vectors(3)
    run_id = algo.publish_zones(PARCEL, vectors)["run_id"]
    sent = _sent(algo)
    assert len(sent) == 3
    for ent, vec in zip(sent, vectors):
        assert ent["meanValue"] == {"type": "Property", "value": vec["properties"]["mean_value"]}
        assert ent["zoningRunId"] == {"type": "Property", "value": run_id}
    assert len({e["zoningRunId"]["value"] for e in sent}) == 1


def test_run_id_differs_between_runs():
    algo = _algo()
    a = algo.publish_zones(PARCEL, _vectors(2))["run_id"]
    assert a != algo.publish_zones(PARCEL, _vectors(2))["run_id"]


def test_stale_zones_are_deleted_and_current_kept():
    algo = _algo(existing_ids=[f"{PREFIX}{k}" for k in range(8)])
    algo.publish_zones(PARCEL, _vectors(3))
    algo.fiware.list_entity_ids.assert_called_once_with(
        "AgriManagementZone", f'hasAgriParcel=="{PARCEL}"'
    )
    deleted = [c.args[0] for c in algo.fiware.delete_entity.call_args_list]
    assert sorted(deleted) == sorted(f"{PREFIX}{k}" for k in range(3, 8))


def test_other_parcel_zones_never_deleted():
    algo = _algo(existing_ids=["urn:ngsi-ld:AgriManagementZone:other:Z5", f"{PREFIX}4"])
    algo.publish_zones(PARCEL, _vectors(3))
    deleted = [c.args[0] for c in algo.fiware.delete_entity.call_args_list]
    assert deleted == [f"{PREFIX}4"]


def test_other_delete_error_is_logged_and_does_not_fail(caplog):
    algo = _algo(existing_ids=[f"{PREFIX}3", f"{PREFIX}4"],
                 delete_side_effect=[RuntimeError("boom"), True])
    with caplog.at_level(logging.WARNING):
        algo.publish_zones(PARCEL, _vectors(3))
    assert algo.fiware.delete_entity.call_count == 2
    assert any(r.levelno == logging.WARNING and f"{PREFIX}3" in r.getMessage()
               for r in caplog.records)


def test_list_failure_is_logged_and_does_not_fail(caplog):
    algo = _algo()
    algo.fiware.list_entity_ids.side_effect = RuntimeError("down")
    with caplog.at_level(logging.WARNING):
        algo.publish_zones(PARCEL, _vectors(2))
    assert len(_sent(algo)) == 2
    algo.fiware.delete_entity.assert_not_called()
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_all_failed_reports_zero_and_deletes_nothing():
    algo = _algo(existing_ids=[f"{PREFIX}{k}" for k in range(8)])
    algo.fiware.create_entity.return_value = False
    out = algo.publish_zones(PARCEL, _vectors(3))
    assert out["published"] == 0 and out["failed"] == 3
    algo.fiware.list_entity_ids.assert_not_called()
    algo.fiware.delete_entity.assert_not_called()


def test_create_exception_counts_as_failure():
    algo = _algo()
    algo.fiware.create_entity.side_effect = RuntimeError("down")
    out = algo.publish_zones(PARCEL, _vectors(2))
    assert out["published"] == 0 and out["failed"] == 2


def test_partial_failure_is_counted_and_still_cleans():
    algo = _algo(existing_ids=[f"{PREFIX}5"])
    algo.fiware.create_entity.side_effect = [True, False, True]
    out = algo.publish_zones(PARCEL, _vectors(3))
    assert out["published"] == 2 and out["failed"] == 1
    algo.fiware.delete_entity.assert_called_once_with(f"{PREFIX}5")


def test_generate_zones_errors_when_nothing_published(monkeypatch):
    import asyncio
    import app.jobs.zoning_algorithm as za
    algo = _algo()
    algo.fiware.create_entity.return_value = False
    monkeypatch.setattr(za, "get_db_session", lambda: iter([_FakeDb()]))
    monkeypatch.setattr(za, "create_storage_service", lambda **k: MagicMock())
    monkeypatch.setattr(za.rasterio, "open", lambda p: _FakeSrc(), raising=False)
    monkeypatch.setattr(za.os.path, "exists", lambda p: False)
    monkeypatch.setattr(algo, "cluster_raster", lambda d, n: (object(), object()))
    monkeypatch.setattr(algo, "vectorize_zones", lambda *a: _vectors(2))
    res = asyncio.run(algo.generate_zones(PARCEL, 2))
    assert res["status"] == "error"


class _FakeSrc:
    transform = None
    crs = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, i):
        return None


class _FakeDb:
    def query(self, *a):
        return self

    def filter(self, *a):
        return self

    def params(self, **k):
        return self

    def order_by(self, *a):
        return self

    def first(self):
        return MagicMock(result={"raster_path": "x.tif"})

    def close(self):
        pass


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _client():
    c = FIWAREClient("http://orion.invalid", tenant_id="acme")
    c.session = MagicMock()
    return c


def test_client_delete_tolerates_404():
    c = _client()
    c.session.delete.return_value = _Resp(404)
    assert c.delete_entity(f"{PREFIX}5") is False


def test_client_delete_raises_on_other_errors():
    c = _client()
    c.session.delete.return_value = _Resp(500)
    try:
        c.delete_entity(f"{PREFIX}5")
    except requests.HTTPError:
        return
    raise AssertionError("expected HTTPError")


def test_client_list_paginates_past_default_page():
    c = _client()
    page1 = [{"id": f"{PREFIX}{k}"} for k in range(100)]
    page2 = [{"id": f"{PREFIX}{k}"} for k in range(100, 130)]
    c.session.get.side_effect = [_Resp(200, page1), _Resp(200, page2)]
    ids = c.list_entity_ids("AgriManagementZone", f'hasAgriParcel=="{PARCEL}"')
    assert len(ids) == 130
    offsets = [call.kwargs["params"]["offset"] for call in c.session.get.call_args_list]
    assert offsets == [0, 100]
    assert c.session.get.call_args_list[0].kwargs["params"]["limit"] == 100


def test_client_create_posts_json_without_ld_context():
    c = _client()
    c.session.post.return_value = _Resp(201)
    assert c.create_entity({"id": f"{PREFIX}0", "type": "AgriManagementZone"}) is True
    kw = c.session.post.call_args.kwargs
    assert kw["headers"]["Content-Type"] == "application/json"
    assert "@context" not in kw["json"]


def test_client_create_409_appends_attrs_via_post():
    c = _client()
    c.session.post.side_effect = [_Resp(409), _Resp(204)]
    ent = {"id": f"{PREFIX}0", "type": "AgriManagementZone",
           "meanValue": {"type": "Property", "value": 0.5}}
    assert c.create_entity(ent) is True
    first, second = c.session.post.call_args_list
    assert second.args[0].endswith(f"/ngsi-ld/v1/entities/{PREFIX}0/attrs")
    assert second.kwargs["json"] == {"meanValue": {"type": "Property", "value": 0.5}}
    assert second.kwargs["headers"]["Content-Type"] == "application/json"
    c.session.patch.assert_not_called()


def test_client_create_409_then_207_is_false():
    c = _client()
    c.session.post.side_effect = [_Resp(409), _Resp(207)]
    assert c.create_entity({"id": f"{PREFIX}0", "type": "T", "a": {"type": "Property", "value": 1}}) is False



def test_client_list_get_headers_and_params():
    c = _client()
    c.session.get.return_value = _Resp(200, [])
    c.list_entity_ids("AgriManagementZone", f'hasAgriParcel=="{PARCEL}"')
    kw = c.session.get.call_args.kwargs
    assert "Content-Type" in kw["headers"] and kw["headers"]["Content-Type"] is None
    assert "attrs" not in kw["params"]
    assert kw["params"]["q"] == f'hasAgriParcel=="{PARCEL}"'


def test_client_session_keeps_link_when_context_set(monkeypatch):
    monkeypatch.setenv("CONTEXT_URL", "http://ctx.invalid/c.json")
    c = FIWAREClient("http://orion.invalid", tenant_id="acme")
    assert "Link" in c.session.headers


def test_client_get_drops_content_type_on_real_request(monkeypatch):
    monkeypatch.setenv("CONTEXT_URL", "http://ctx.invalid/c.json")
    c = FIWAREClient("http://orion.invalid", tenant_id="acme")
    req = requests.Request("GET", "http://orion.invalid/x",
                           headers={"Content-Type": None, "Accept": "application/json"})
    prepared = c.session.prepare_request(req)
    assert "Content-Type" not in prepared.headers
    assert "Link" in prepared.headers


def test_client_query_entities_get_has_no_content_type():
    c = _client()
    c.session.get.return_value = _Resp(200, [])
    c.query_entities("AgriManagementZone", {"a": "b"})
    assert c.session.get.call_args.kwargs["headers"]["Content-Type"] is None


def test_client_create_generic_exception_does_not_patch():
    c = _client()
    c.session.post.side_effect = requests.ConnectionError("down")
    assert c.create_entity({"id": f"{PREFIX}0", "type": "AgriManagementZone"}) is False
    c.session.patch.assert_not_called()


# ---- vectorize_zones: one entity per cluster, WGS84 ----------------------------

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import shape as _shape


def _roundtrip(tmp_path, labels, epsg, x0, y0, px):
    """Write a synthetic raster with rasterio and read crs/transform back."""
    path = str(tmp_path / "r.tif")
    with rasterio.open(path, "w", driver="GTiff", height=labels.shape[0],
                       width=labels.shape[1], count=1, dtype="float32",
                       crs=CRS.from_epsg(epsg), transform=from_origin(x0, y0, px, px)) as dst:
        dst.write(np.zeros(labels.shape, dtype="float32"), 1)
    with rasterio.open(path) as src:
        return src.crs, src.transform


def _algo_v():
    return ZoningAlgorithm.__new__(ZoningAlgorithm)


def test_split_cluster_gives_one_multipolygon_with_summed_area(tmp_path):
    labels = np.zeros((10, 30), dtype=int)
    labels[:, 12:18] = 1          # cluster 1 splits cluster 0 in two patches
    ndvi = np.where(labels == 0, 0.4, 0.8).astype("float64")
    crs, tr = _roundtrip(tmp_path, labels, 32630, 500000.0, 4600000.0, 10.0)
    feats = _algo_v().vectorize_zones(labels, tr, ndvi, np.array([0.4, 0.8]), crs)
    assert [f["properties"]["zone_id"] for f in feats] == [0, 1]
    z0 = feats[0]
    assert z0["geometry"]["type"] == "MultiPolygon"
    assert len(z0["geometry"]["coordinates"]) == 2
    # 10x24 px of 10 m = 2.4 ha (whole cluster, both patches)
    assert z0["properties"]["area_ha"] == 2.4
    assert z0["properties"]["mean_value"] == 0.4
    assert feats[1]["geometry"]["type"] == "Polygon"


def test_utm_raster_is_published_as_lon_lat(tmp_path):
    labels = np.zeros((20, 20), dtype=int)
    labels[:, 10:] = 1
    ndvi = np.full(labels.shape, 0.5)
    crs, tr = _roundtrip(tmp_path, labels, 32630, 500000.0, 4600000.0, 10.0)
    feats = _algo_v().vectorize_zones(labels, tr, ndvi, np.array([0.4, 0.8]), crs)
    for f in feats:
        minx, miny, maxx, maxy = _shape(f["geometry"]).bounds
        assert -3.01 < minx and maxx < -2.99
        assert 41.54 < miny and maxy < 41.57


def test_geographic_raster_area_in_hectares(tmp_path):
    labels = np.zeros((10, 10), dtype=int)
    ndvi = np.full(labels.shape, 0.5)
    px = 0.0001  # ~ 8.3 m x 11 m at 41 N
    crs, tr = _roundtrip(tmp_path, labels, 4326, -3.0, 41.5, px)
    f = _algo_v().vectorize_zones(labels, tr, ndvi, np.array([0.5]), crs)[0]
    assert 0.8 < f["properties"]["area_ha"] < 1.0   # 100 px * ~93 m2


def test_stair_stepped_boundary_is_simplified(tmp_path):
    n = 40
    rows, cols = np.indices((n, n))
    labels = (cols > rows).astype(int)       # stair-stepped diagonal
    ndvi = np.full(labels.shape, 0.5)
    crs, tr = _roundtrip(tmp_path, labels, 32630, 500000.0, 4600000.0, 10.0)
    from rasterio.features import shapes as _shapes
    raw = sum(len(g["coordinates"][0]) for g, v in
              _shapes(labels.astype("int32"), transform=tr) if v == 1)
    f = [x for x in _algo_v().vectorize_zones(labels, tr, ndvi, np.array([0.4, 0.8]), crs)
         if x["properties"]["zone_id"] == 1][0]
    out = len(f["geometry"]["coordinates"][0])
    assert out < raw
