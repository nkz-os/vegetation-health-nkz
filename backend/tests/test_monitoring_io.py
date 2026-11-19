"""Timeline read with the parcel's own crop as the fallback for woody crops without a sowing."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import monitoring_io as mio

PARCEL = "urn:ngsi-ld:AgriParcel:p1"
CROP = "urn:ngsi-ld:AgriCrop:c1"
REGISTERED = {"current": {"start": {"date": "2026-03-10", "provenance": "actual"}, "end": {}}, "previous": None}
EMPTY = {"current": None, "previous": None}


def status_error(code):
    request = httpx.Request("GET", "http://broker/x")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(code, request=request))


class FakeOrion:
    entities = {}
    errors = {}
    gets = []
    closed = 0

    def __init__(self, tenant_id, *a, **k):
        self.tenant_id = tenant_id

    async def get_entity(self, entity_id, options=None):
        FakeOrion.gets.append((entity_id, options))
        if entity_id in FakeOrion.errors:
            raise FakeOrion.errors[entity_id]
        return FakeOrion.entities[entity_id]

    async def close(self):
        FakeOrion.closed += 1


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeOrion.entities, FakeOrion.errors, FakeOrion.gets, FakeOrion.closed = {}, {}, [], 0
    monkeypatch.setattr(mio, "OrionClient", FakeOrion)
    monkeypatch.setattr(mio, "fetch_crop_timeline", AsyncMock(return_value=EMPTY))


def read():
    return asyncio.run(mio.fetch_timeline("tenant-a", PARCEL))


def test_a_registered_cycle_needs_no_second_read():
    mio.fetch_crop_timeline.return_value = REGISTERED
    assert read() == REGISTERED
    assert FakeOrion.gets == []


def test_a_failed_timeline_is_a_failed_read_and_nothing_more_is_read():
    mio.fetch_crop_timeline.return_value = None
    assert read() is None
    assert FakeOrion.gets == []


def test_without_a_usable_cycle_the_parcel_s_own_crop_is_attached():
    FakeOrion.entities = {
        PARCEL: {"id": PARCEL, "hasAgriCrop": CROP},
        CROP: {"id": CROP, "species": "Olea europaea", "cropLifecycle": "perennial", "status": "active",
               "terminationDate": "2031-01-01"},
    }
    timeline = read()
    assert timeline["parcel_crop"] == {"species": "Olea europaea", "lifecycle": "perennial", "status": "active",
                                       "end": {"date": "2031-01-01", "provenance": None}}
    assert [g[0] for g in FakeOrion.gets] == [PARCEL, CROP] and FakeOrion.closed == 1
    assert mio.fetch_crop_timeline.return_value == EMPTY  # the SDK's answer was not mutated


def test_actual_termination_wins_and_names_may_be_iris_or_legacy():
    FakeOrion.entities = {
        PARCEL: {"id": PARCEL, "refAgriCrop": {"type": "Relationship", "object": CROP}},
        CROP: {"id": CROP, "https://uri.etsi.org/ngsi-ld/default-context/species": "Vitis vinifera",
               "https://nkz-os.org/ns/cropLifecycle": {"type": "Property", "value": "unknown"},
               "actualTerminationDate": "2026-05-01", "terminationDate": "2027-01-01"},
    }
    crop = read()["parcel_crop"]
    assert crop["species"] == "Vitis vinifera" and crop["lifecycle"] == "unknown"
    assert crop["end"]["date"] == "2026-05-01" and crop["status"] is None


def test_a_parcel_with_no_crop_assigned_has_no_fallback_crop():
    FakeOrion.entities = {PARCEL: {"id": PARCEL}}
    assert read()["parcel_crop"] is None


@pytest.mark.parametrize("missing", [PARCEL, CROP])
def test_a_missing_entity_is_no_crop_not_a_failure(missing):
    FakeOrion.entities = {PARCEL: {"id": PARCEL, "hasAgriCrop": CROP}}
    FakeOrion.errors = {missing: status_error(404)}
    assert read()["parcel_crop"] is None


@pytest.mark.parametrize("error", [status_error(503), httpx.ConnectError("down"), RuntimeError("odd")])
def test_a_failed_fallback_read_is_a_failed_read(error):
    FakeOrion.entities = {PARCEL: {"id": PARCEL, "hasAgriCrop": CROP}}
    FakeOrion.errors = {CROP: error}
    assert read() is None
    assert FakeOrion.closed == 1
