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


def test_client_create_409_falls_back_to_patch():
    c = _client()
    c.session.post.return_value = _Resp(409)
    c.session.patch.return_value = _Resp(204)
    assert c.create_entity({"id": f"{PREFIX}0", "type": "AgriManagementZone"}) is True
    c.session.patch.assert_called_once()
