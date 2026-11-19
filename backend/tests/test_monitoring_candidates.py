"""Parcels that need activation: crop reads, concurrency, cache."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import monitoring_candidates as mc
from app.services.observation_series import BrokerUnavailable

TODAY = date(2026, 11, 19)
TENANT = "tenant-a"


def urn(n):
    return f"urn:ngsi-ld:AgriParcel:p{n}"


def sown_timeline(species="Triticum aestivum", lifecycle="annual", end=None):
    cycle = {"lifecycle": lifecycle, "species": species,
             "start": {"date": "2026-03-10", "provenance": "actual"},
             "end": {"date": end, "provenance": "actual" if end else None}}
    return {"current": None if end else cycle, "previous": cycle if end else None}


class FakeOrion:
    """Async SDK client surface used by the parcel listing."""
    pages = []
    calls = []
    closed = 0

    def __init__(self, tenant_id, *a, **k):
        self.tenant_id = tenant_id

    async def query_entities(self, type=None, q=None, limit=100, offset=0, attrs=None, options=None):
        FakeOrion.calls.append({"type": type, "limit": limit, "offset": offset, "options": options})
        index = offset // limit
        return FakeOrion.pages[index] if index < len(FakeOrion.pages) else []

    async def close(self):
        FakeOrion.closed += 1


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    FakeOrion.pages, FakeOrion.calls, FakeOrion.closed = [], [], 0
    monkeypatch.setattr(mc, "OrionClient", FakeOrion)
    monkeypatch.setattr(mc, "PAGE_SIZE", 2)
    mc.invalidate(TENANT)
    yield
    mc.invalidate(TENANT)


class FakeDb:
    """Session returning the subscriptions and continuity requests of the tenant."""

    def __init__(self, subs=(), requests=()):
        self.subs, self.requests = list(subs), list(requests)
        self._model = None

    def query(self, model):
        self._model = model
        return self

    def filter(self, *_a):
        return self

    def all(self):
        return self.subs if self._model.__tablename__ == "vegetation_subscriptions" else self.requests


def sub(entity, active=True, reason=None):
    return SimpleNamespace(entity_id=entity, is_active=active, paused_reason=reason, start_date=date(2026, 3, 10))


def req(entity, module):
    return SimpleNamespace(entity_id=entity, module_id=module)


# ── listing parcels with a crop ──────────────────────────────────────────

def test_lists_parcels_with_a_crop_across_pages_and_vocabularies():
    FakeOrion.pages = [
        [{"id": urn(1), "name": "North", "hasAgriCrop": "urn:ngsi-ld:AgriCrop:c1"},
         {"id": urn(2), "name": "No crop"}],
        [{"id": urn(3), "https://uri.etsi.org/ngsi-ld/default-context/name": "Long",
          "https://nkz-os.org/ns/hasAgriCrop": {"type": "Relationship", "object": "urn:ngsi-ld:AgriCrop:c3"}},
         {"id": urn(4), "name": {"type": "Property", "value": "Legacy"},
          "refAgriCrop": "urn:ngsi-ld:AgriCrop:c4"}],
        [{"id": urn(5), "name": "Empty rel", "hasAgriCrop": None}],
    ]
    found = asyncio.run(mc.parcels_with_crop(TENANT))
    assert found == [{"id": urn(1), "name": "North"}, {"id": urn(3), "name": "Long"},
                     {"id": urn(4), "name": "Legacy"}]
    assert [c["offset"] for c in FakeOrion.calls] == [0, 2, 4]
    assert all(c["type"] == "AgriParcel" and c["options"] == "keyValues" for c in FakeOrion.calls)
    assert FakeOrion.closed == 1


def test_a_broker_error_while_listing_is_unavailable(monkeypatch):
    async def boom(self, *a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(FakeOrion, "query_entities", boom)
    with pytest.raises(BrokerUnavailable):
        asyncio.run(mc.parcels_with_crop(TENANT))
    assert FakeOrion.closed == 1


# ── candidates ───────────────────────────────────────────────────────────

def patch_sources(monkeypatch, parcels, timelines):
    monkeypatch.setattr(mc, "parcels_with_crop", AsyncMock(return_value=parcels))
    fetch = AsyncMock(side_effect=lambda tenant, parcel: timelines[parcel])
    monkeypatch.setattr(mc, "fetch_timeline", fetch)
    return fetch


def test_candidates_are_alive_crops_without_a_subscription(monkeypatch):
    parcels = [{"id": urn(i), "name": f"P{i}"} for i in range(1, 6)]
    timelines = {
        urn(1): sown_timeline(),                                              # candidate
        urn(2): sown_timeline(),                                              # active: not a candidate
        urn(3): sown_timeline(),                                              # paused by a person: not
        urn(4): {"current": {"lifecycle": "annual", "species": "Zea mays",    # planned only: not
                             "start": {"date": "2026-04-01", "provenance": "planned"},
                             "end": {"date": None, "provenance": None}}, "previous": None},
        urn(5): sown_timeline("Olea europaea", "unknown"),                    # olive: candidate
    }
    patch_sources(monkeypatch, parcels, timelines)
    db = FakeDb(subs=[sub(urn(2)), sub(urn(3), False, "user")])
    answer = asyncio.run(mc.candidates(db, TENANT, TODAY))
    assert answer["partial"] is False and answer["failed"] == 0
    result = answer["candidates"]
    assert [c["entity_id"] for c in result] == [urn(1), urn(5)]
    assert result[0] == {"entity_id": urn(1), "name": "P1", "species": "Triticum aestivum",
                         "lifecycle": "annual", "period": {"start": "2026-03-10", "end": None}}
    assert result[1]["lifecycle"] == "perennial" and result[1]["period"]["start"] == "2025-11-19"


def test_a_harvested_parcel_kept_continuous_is_not_a_candidate(monkeypatch):
    patch_sources(monkeypatch, [{"id": urn(1), "name": "P1"}], {urn(1): sown_timeline(end="2026-07-05")})
    db = FakeDb(requests=[req(urn(1), "carbon")])
    assert asyncio.run(mc.candidates(db, TENANT, TODAY))["candidates"] == []


def test_a_failed_parcel_is_skipped_and_reported_not_fatal(monkeypatch, caplog):
    patch_sources(monkeypatch, [{"id": urn(1), "name": "a"}, {"id": urn(2), "name": "b"}, {"id": urn(3), "name": "c"}],
                  {urn(1): sown_timeline(), urn(2): None, urn(3): sown_timeline()})
    with caplog.at_level("WARNING"):
        answer = asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert [c["entity_id"] for c in answer["candidates"]] == [urn(1), urn(3)]
    assert answer["partial"] is True and answer["failed"] == 1
    assert any(TENANT in m and urn(2) in m for m in caplog.messages)


def test_every_parcel_failing_is_still_an_answer_with_all_failed(monkeypatch):
    patch_sources(monkeypatch, [{"id": urn(1), "name": "a"}], {urn(1): None})
    answer = asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert answer == {"candidates": [], "partial": True, "failed": 1}


def test_a_failure_listing_the_parcels_is_unavailable(monkeypatch):
    monkeypatch.setattr(mc, "parcels_with_crop", AsyncMock(side_effect=BrokerUnavailable("down")))
    with pytest.raises(BrokerUnavailable):
        asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))


def test_a_partial_answer_is_cached_for_one_minute_only(monkeypatch):
    fetch = patch_sources(monkeypatch, [{"id": urn(1), "name": "a"}, {"id": urn(2), "name": "b"}],
                          {urn(1): sown_timeline(), urn(2): None})
    clock = {"t": 1000.0}
    monkeypatch.setattr(mc.time, "monotonic", lambda: clock["t"])
    asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert fetch.await_count == 2
    clock["t"] += 59
    asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert fetch.await_count == 2
    clock["t"] += 2
    asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert fetch.await_count == 4


def test_crop_reads_run_at_most_five_at_a_time(monkeypatch):
    parcels = [{"id": urn(i), "name": str(i)} for i in range(12)]
    monkeypatch.setattr(mc, "parcels_with_crop", AsyncMock(return_value=parcels))
    state = {"now": 0, "max": 0}

    async def slow(tenant, parcel):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return sown_timeline()

    monkeypatch.setattr(mc, "fetch_timeline", slow)
    result = asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))["candidates"]
    assert len(result) == 12 and state["max"] == 5


def test_the_answer_is_cached_per_tenant_for_five_minutes_and_can_be_invalidated(monkeypatch):
    fetch = patch_sources(monkeypatch, [{"id": urn(1), "name": "a"}], {urn(1): sown_timeline()})
    clock = {"t": 1000.0}
    monkeypatch.setattr(mc.time, "monotonic", lambda: clock["t"])
    first = asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY)) == first
    assert fetch.await_count == 1
    mc.invalidate("tenant-b")  # another tenant leaves it alone
    asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert fetch.await_count == 1
    clock["t"] += 299
    asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert fetch.await_count == 1
    clock["t"] += 2
    asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert fetch.await_count == 2
    mc.invalidate(TENANT)
    asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    assert fetch.await_count == 3


def test_a_failed_listing_is_not_cached(monkeypatch):
    monkeypatch.setattr(mc, "parcels_with_crop", AsyncMock(side_effect=BrokerUnavailable("down")))
    with pytest.raises(BrokerUnavailable):
        asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))
    patch_sources(monkeypatch, [{"id": urn(1), "name": "a"}], {urn(1): sown_timeline()})
    assert len(asyncio.run(mc.candidates(FakeDb(), TENANT, TODAY))["candidates"]) == 1


# ── one parcel ───────────────────────────────────────────────────────────

def test_parcel_state_reads_timeline_subscription_and_requests(monkeypatch):
    monkeypatch.setattr(mc, "fetch_timeline", AsyncMock(return_value=sown_timeline()))
    monkeypatch.setattr(mc, "continuous_modules", MagicMock(return_value=["carbon"]))

    class OneSub(FakeDb):
        def first(self):
            return sub(urn(1))

    state = asyncio.run(mc.parcel_state(OneSub(), TENANT, urn(1), TODAY))
    assert state["monitoring"] == "active" and state["continuous_by"] == ["carbon"]
    assert state["crop"]["present"] is True


def test_parcel_state_with_a_failed_crop_read_is_unavailable(monkeypatch):
    monkeypatch.setattr(mc, "fetch_timeline", AsyncMock(return_value=None))
    with pytest.raises(BrokerUnavailable):
        asyncio.run(mc.parcel_state(FakeDb(), TENANT, urn(1), TODAY))
