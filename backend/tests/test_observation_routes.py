"""The series routes: user (gateway identity) and internal (shared secret), plus NDMI in new subscriptions."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import internal, parcels
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
    with patch.object(parcels, "_get_crop_species_from_orion", AsyncMock(return_value=None)) as m:
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
    with patch.object(parcels, "_get_crop_species_from_orion", AsyncMock(return_value=name)):
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
