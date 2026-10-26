"""Tests for the parcel activation endpoint called by entity-manager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import internal

URL = "/api/internal/setup-parcel"
HEADERS = {"X-Internal-Service-Secret": "s3"}
GEOMETRY = {"type": "Polygon", "coordinates": [[[-2.0, 42.0], [-2.0, 42.1], [-1.9, 42.1], [-2.0, 42.0]]]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(internal, "INTERNAL_SECRET", "s3")
    monkeypatch.setattr(internal, "SessionLocal", lambda: MagicMock())
    app = FastAPI()
    app.include_router(internal.router)
    with TestClient(app) as c:
        yield c


def test_activate_enqueues_lst_for_the_parcel(client):
    location = AsyncMock(return_value=(GEOMETRY, 42.05, -1.97))
    with patch.object(internal, "_fetch_parcel_location", location), \
         patch.object(internal, "_enqueue_lst") as enqueue:
        r = client.post(URL, json={"parcel_id": "urn:ngsi-ld:AgriParcel:p1", "tenant_id": "t1"}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["lst_enqueued"] is True
    enqueue.assert_called_once_with("t1", "urn:ngsi-ld:AgriParcel:p1", GEOMETRY, 42.05, -1.97)


def test_activate_without_geometry_enqueues_nothing(client):
    location = AsyncMock(return_value=(None, None, None))
    with patch.object(internal, "_fetch_parcel_location", location), \
         patch.object(internal, "_enqueue_lst") as enqueue:
        r = client.post(URL, json={"parcel_id": "urn:ngsi-ld:AgriParcel:p1", "tenant_id": "t1"}, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["lst_enqueued"] is False
    enqueue.assert_not_called()


@pytest.mark.parametrize("action,message", [("deactivate", "deactivated"), ("teardown", "torn_down")])
def test_deactivate_and_teardown_enqueue_nothing(client, action, message):
    with patch.object(internal, "_fetch_parcel_location", AsyncMock()) as location, \
         patch.object(internal, "_enqueue_lst") as enqueue:
        r = client.post(URL, json={"parcel_id": "urn:ngsi-ld:AgriParcel:p1", "tenant_id": "t1", "action": action},
                        headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["message"] == message
    location.assert_not_awaited()
    enqueue.assert_not_called()


def test_unknown_action_returns_400(client):
    r = client.post(URL, json={"parcel_id": "p1", "tenant_id": "t1", "action": "explode"}, headers=HEADERS)
    assert r.status_code == 400


def test_wrong_secret_returns_401(client):
    r = client.post(URL, json={"parcel_id": "p1", "tenant_id": "t1"}, headers={"X-Internal-Service-Secret": "x"})
    assert r.status_code == 401
