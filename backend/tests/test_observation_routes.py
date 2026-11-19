"""The series routes: user (gateway identity) and internal (shared secret), plus NDMI in new subscriptions."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import httpx

from app.api import internal, parcels, scenes
from app.services.observation_series import BrokerUnavailable

SHORT = "0123456789abcdef"
URN = f"urn:ngsi-ld:AgriParcel:{SHORT}"
USER_URL = f"/api/vegetation/parcels/{URN}/observations"
INTERNAL_URL = f"/api/internal/vegetation/parcels/{SHORT}/observations"
BODY = {"entity_id": URN, "index": "NDVI", "rules": {"min_valid_fraction": 0.7, "mask": "interior"}, "points": []}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(internal, "INTERNAL_SECRET", "s3")
    app = FastAPI()
    app.include_router(parcels.router)
    app.include_router(internal.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def series():
    with patch.object(parcels, "observation_series", return_value=dict(BODY)) as m:
        yield m


@pytest.fixture
def species():
    with patch.object(parcels, "_get_crop_species_strict", AsyncMock(return_value=None)) as m:
        yield m


USER = {"X-Tenant-ID": "acme", "X-User-ID": "u1"}
SECRET = {"X-Internal-Service-Secret": "s3"}


def test_user_route_uses_tenant_from_auth(client, series, species):
    r = client.get(USER_URL, params={"index": "ndre", "start": "2026-01-01", "end": "2026-02-01"},
                   headers={**USER, "X-Tenant-ID": "tenant-a"})
    assert r.status_code == 200 and r.json() == BODY
    args, kwargs = series.call_args
    assert args[0] == "tenant-a" and args[1] == URN and args[2] == "NDRE"
    assert str(args[3]) == "2026-01-01" and str(args[4]) == "2026-02-01"
    assert kwargs == {"woody": False}
    species.assert_awaited_once_with("tenant-a", URN)


def test_user_route_requires_identity(client, series, species):
    assert client.get(USER_URL).status_code in (400, 401)
    series.assert_not_called()


def test_short_entity_id_is_completed(client, series, species):
    r = client.get(f"/api/vegetation/parcels/{SHORT}/observations", headers=USER)
    assert r.status_code == 200
    assert series.call_args.args[1] == URN


@pytest.mark.parametrize("name,woody", [("Olea europaea", True), ("Triticum aestivum", False), (None, False)])
def test_woody_comes_from_the_crop_species(client, series, name, woody):
    with patch.object(parcels, "_get_crop_species_strict", AsyncMock(return_value=name)):
        client.get(USER_URL, headers=USER)
    assert series.call_args.kwargs["woody"] is woody


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Service-Secret": "wrong"}, {"X-Internal-Service-Secret": ""}])
def test_internal_route_requires_secret(client, series, species, headers):
    r = client.get(INTERNAL_URL, params={"tenant_id": "acme"}, headers=headers)
    assert r.status_code == 401
    series.assert_not_called()


def test_internal_route_rejects_when_server_secret_is_empty(client, series, species, monkeypatch):
    monkeypatch.setattr(internal, "INTERNAL_SECRET", "")
    r = client.get(INTERNAL_URL, params={"tenant_id": "acme"}, headers={"X-Internal-Service-Secret": ""})
    assert r.status_code == 401


def test_internal_route_requires_tenant(client, series, species):
    assert client.get(INTERNAL_URL, headers=SECRET).status_code == 400
    assert client.get(INTERNAL_URL, params={"tenant_id": ""}, headers=SECRET).status_code == 400
    series.assert_not_called()


def test_internal_route_serves_the_series(client, series, species):
    r = client.get(INTERNAL_URL, params={"tenant_id": "acme", "index": "NDMI"}, headers=SECRET)
    assert r.status_code == 200 and r.json() == BODY
    assert series.call_args.args[:3] == ("acme", URN, "NDMI")


def test_bad_index_400(client, series, species):
    assert client.get(USER_URL, params={"index": "LST"}, headers=USER).status_code == 400
    assert client.get(INTERNAL_URL, params={"tenant_id": "acme", "index": "x"}, headers=SECRET).status_code == 400
    series.assert_not_called()


def test_broker_down_502(client, species):
    with patch.object(parcels, "observation_series", side_effect=BrokerUnavailable("down")):
        r = client.get(USER_URL, headers=USER)
        r2 = client.get(INTERNAL_URL, params={"tenant_id": "acme"}, headers=SECRET)
    assert r.status_code == 502 and r2.status_code == 502
    assert "points" not in r.json()


def test_new_subscription_includes_ndmi():
    assert "NDMI" in internal.SUBSCRIPTION_INDEX_TYPES
    assert internal.SUBSCRIPTION_INDEX_TYPES == ("NDVI", "EVI", "SAVI", "NDRE", "GNDVI", "NDMI")


class FakeOrion:
    """Orion read level: entities by id; an Exception value is raised as the broker failure."""

    def __init__(self, entities):
        self.entities = entities

    async def get_entity(self, entity_id, options=None):
        value = self.entities[entity_id]
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self):
        pass


def _orion_with_crop(species):
    return FakeOrion({
        URN: {"hasAgriCrop": {"type": "Relationship", "object": "urn:ngsi-ld:AgriCrop:c1"}},
        "urn:ngsi-ld:AgriCrop:c1": {"cropSpecies": {"type": "Property", "value": species}},
    })


def _real_series(entity_points=None):
    product = {
        "id": "urn:ngsi-ld:EOProduct:acme:0123456789:2026-05-01", "type": "EOProduct",
        "hasAgriParcel": {"type": "Relationship", "object": URN},
        "sensingDate": {"type": "Property", "value": "2026-05-01"},
        "ndvi": {"type": "Property", "value": 0.4,
                 "interiorValidFraction": {"type": "Property", "value": 0.9},
                 "interiorPixelCount": {"type": "Property", "value": 50},
                 "interiorMask": {"type": "Property", "value": "core"},
                 "interiorMedian": {"type": "Property", "value": 0.5}},
    }
    return patch("app.services.observation_series.fetch_parcel_products", return_value=[product])


@pytest.mark.parametrize("species,basis", [("Olea europaea", "parcel_interior"), ("Triticum aestivum", None)])
def test_woody_end_to_end_from_the_broker_read(client, species, basis):
    with patch.object(scenes, "OrionClient", return_value=_orion_with_crop(species)), _real_series():
        r = client.get(USER_URL, headers=USER)
    assert r.status_code == 200
    assert r.json().get("canopy_basis") == basis
    assert r.json()["points"][0]["quality"] == "ok"


def test_parcel_without_crop_is_not_woody(client):
    orion = FakeOrion({URN: {"id": URN}})
    with patch.object(scenes, "OrionClient", return_value=orion), _real_series():
        r = client.get(USER_URL, headers=USER)
    assert r.status_code == 200 and "canopy_basis" not in r.json()


def test_crop_lookup_failure_is_502_not_a_silent_non_woody_series(client):
    orion = FakeOrion({URN: RuntimeError("connection refused")})
    with patch.object(scenes, "OrionClient", return_value=orion), _real_series():
        r = client.get(USER_URL, headers=USER)
        r2 = client.get(INTERNAL_URL, params={"tenant_id": "acme"}, headers=SECRET)
    assert r.status_code == 502 and r2.status_code == 502


def test_crop_lookup_http_500_is_502(client):
    response = httpx.Response(500, request=httpx.Request("GET", "http://orion"))
    orion = FakeOrion({URN: httpx.HTTPStatusError("boom", request=response.request, response=response)})
    with patch.object(scenes, "OrionClient", return_value=orion), _real_series():
        assert client.get(USER_URL, headers=USER).status_code == 502


def test_non_ascii_secret_header_is_401(client, series, species):
    r = client.get(INTERNAL_URL, params={"tenant_id": "acme"},
                   headers={"X-Internal-Service-Secret": "s\u00e9cret".encode("latin-1")})
    assert r.status_code == 401
    series.assert_not_called()
