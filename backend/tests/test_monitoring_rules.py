"""Monitoring period and state of a parcel, from the crop timeline (pure rules)."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.monitoring import Period, is_woody, monitoring_period, monitoring_state, needs_parcel_crop, utc_today

TODAY = date(2026, 11, 19)
SOWN = date(2026, 3, 10)
HARVESTED = date(2026, 7, 5)


def boundary(day=None, provenance=None):
    return {"date": day.isoformat() if day else None, "provenance": provenance}


def cycle(lifecycle="annual", species="Triticum aestivum", start=None, end=None):
    return {
        "crop_id": "urn:ngsi-ld:AgriCrop:c1", "role": "main_crop", "species": species, "status": "active",
        "lifecycle": lifecycle, "start": start or boundary(), "end": end or boundary(),
    }


def timeline(current=None, previous=None):
    return {"current": current, "previous": previous}


def sown(provenance="actual", **kw):
    return cycle(start=boundary(SOWN, provenance), **kw)


def sub(is_active=True, paused_reason=None, start_date=SOWN):
    return SimpleNamespace(is_active=is_active, paused_reason=paused_reason, start_date=start_date)


# ── is_woody ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lifecycle,species,expected", [
    ("perennial", "Something odd", True),
    ("annual", "Olea europaea", False),
    ("unknown", "Olea europaea", True),
    (None, "Vitis vinifera", True),
    ("unknown", "Triticum aestivum", False),
    ("unknown", None, False),
])
def test_is_woody(lifecycle, species, expected):
    assert is_woody({"lifecycle": lifecycle, "species": species}) is expected


# ── monitoring_period ────────────────────────────────────────────────────

def test_no_timeline_or_no_crop_has_no_period():
    assert monitoring_period(None, TODAY, False) is None
    assert monitoring_period(timeline(), TODAY, False) is None
    assert monitoring_period(timeline(), TODAY, True) is None


@pytest.mark.parametrize("provenance", ["actual", "declared"])
def test_annual_sown_runs_from_the_sowing_and_is_open(provenance):
    assert monitoring_period(timeline(current=sown(provenance)), TODAY, False) == Period(SOWN, None, "annual")


def test_planned_sowing_only_has_no_period():
    planned = cycle(start=boundary(SOWN, "planned"))
    assert monitoring_period(timeline(current=planned), TODAY, False) is None
    assert monitoring_period(timeline(previous=planned), TODAY, True) is None


def test_annual_ends_on_the_registered_harvest():
    harvested = sown(end=boundary(HARVESTED, "actual"))
    assert monitoring_period(timeline(previous=harvested), TODAY, False) == Period(SOWN, HARVESTED, "annual")


def test_a_planned_end_does_not_close_the_period():
    planned_end = sown(end=boundary(HARVESTED, "planned"))
    assert monitoring_period(timeline(current=planned_end), TODAY, False) == Period(SOWN, None, "annual")


def test_unknown_lifecycle_non_tree_is_annual():
    assert monitoring_period(timeline(current=sown(lifecycle="unknown")), TODAY, False).kind == "annual"


def test_olive_without_lifecycle_is_continuous_and_never_paused_by_its_harvest():
    olive = cycle(lifecycle="unknown", species="Olea europaea", start=boundary(date(2015, 1, 1), "actual"),
                  end=boundary(HARVESTED, "actual"))
    expected = Period(date(2025, 11, 19), None, "woody")
    assert monitoring_period(timeline(previous=olive), TODAY, False) == expected
    assert monitoring_period(timeline(current=olive), TODAY, False) == expected


def test_perennial_window_is_the_last_365_days():
    perennial = cycle(lifecycle="perennial", species="X", start=boundary(date(2015, 1, 1), "declared"))
    assert monitoring_period(timeline(current=perennial), TODAY, False) == Period(date(2025, 11, 19), None, "woody")


def test_continuous_request_keeps_an_harvested_annual_open():
    harvested = sown(end=boundary(HARVESTED, "actual"))
    assert monitoring_period(timeline(previous=harvested), TODAY, True) == Period(date(2025, 11, 19), None, "continuous")


def test_continuous_without_a_crop_has_no_period():
    assert monitoring_period(timeline(), TODAY, True) is None


def test_the_current_cycle_wins_over_the_previous():
    previous = sown(end=boundary(HARVESTED, "actual"))
    resown = cycle(start=boundary(date(2026, 10, 20), "actual"))
    assert monitoring_period(timeline(current=resown, previous=previous), TODAY, False) == \
        Period(date(2026, 10, 20), None, "annual")


# ── monitoring_state ─────────────────────────────────────────────────────

@pytest.mark.parametrize("row,expected", [
    (None, "none"),
    (sub(True), "active"),
    (sub(False, "harvest"), "paused_harvest"),
    (sub(False, "user"), "paused_user"),
    (sub(False, None), "paused_user"),  # an old pause with no reason is a person's
])
def test_monitoring_follows_the_subscription(row, expected):
    state = monitoring_state(timeline(current=sown()), row, [], TODAY)
    assert state["monitoring"] == expected


def test_state_shape_for_a_sown_annual_without_subscription(monkeypatch):
    monkeypatch.delenv("VEG_ACTIVATION_ROLES", raising=False)
    state = monitoring_state(timeline(current=sown()), None, [], TODAY)
    assert state == {
        "crop": {"present": True, "species": "Triticum aestivum", "lifecycle": "annual",
                 "planting_date": "2026-03-10", "termination_date": None},
        "monitoring": "none",
        "period": {"start": "2026-03-10", "end": None},
        "needs_activation": True,
        "continuous_by": [],
        "activation_roles": ["Farmer", "TechnicalConsultant", "TenantAdmin", "PlatformAdmin"],
    }


def test_activation_roles_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("VEG_ACTIVATION_ROLES", " Farmer , TenantAdmin ,")
    state = monitoring_state(timeline(), None, [], TODAY)
    assert state["activation_roles"] == ["Farmer", "TenantAdmin"]


def test_no_crop_state():
    state = monitoring_state(timeline(), None, [], TODAY)
    assert state["crop"] == {"present": False, "species": None, "lifecycle": None,
                             "planting_date": None, "termination_date": None}
    assert state["period"] == {"start": None, "end": None}
    assert state["needs_activation"] is False


def test_planned_sowing_is_not_a_crop_and_asks_for_nothing():
    state = monitoring_state(timeline(current=cycle(start=boundary(SOWN, "planned"))), None, [], TODAY)
    assert state["crop"]["present"] is False and state["crop"]["planting_date"] is None
    assert state["needs_activation"] is False and state["period"]["start"] is None


def test_harvested_annual_is_not_alive_so_it_needs_no_activation():
    state = monitoring_state(timeline(previous=sown(end=boundary(HARVESTED, "actual"))), None, [], TODAY)
    assert state["crop"]["present"] is False
    assert state["crop"]["termination_date"] == "2026-07-05"
    assert state["period"] == {"start": "2026-03-10", "end": "2026-07-05"}
    assert state["needs_activation"] is False


def test_olive_is_alive_and_reported_as_perennial():
    olive = cycle(lifecycle="unknown", species="Olea europaea", start=boundary(date(2015, 1, 1), "actual"))
    state = monitoring_state(timeline(current=olive), None, [], TODAY)
    assert state["crop"]["present"] is True and state["crop"]["lifecycle"] == "perennial"
    assert state["needs_activation"] is True


def test_uprooted_perennial_is_not_alive():
    gone = cycle(lifecycle="perennial", start=boundary(date(2015, 1, 1), "actual"),
                 end=boundary(date(2026, 5, 1), "actual"))
    state = monitoring_state(timeline(previous=gone), None, [], TODAY)
    assert state["crop"]["present"] is False and state["period"] == {"start": None, "end": None}


def test_active_or_paused_subscriptions_never_need_activation():
    for row in (sub(True), sub(False, "harvest"), sub(False, "user")):
        assert monitoring_state(timeline(current=sown()), row, [], TODAY)["needs_activation"] is False


def test_continuous_by_is_listed_sorted_and_opens_the_period():
    state = monitoring_state(timeline(previous=sown(end=boundary(HARVESTED, "actual"))),
                             sub(False, "harvest"), ["carbon", "alpha"], TODAY)
    assert state["continuous_by"] == ["alpha", "carbon"]
    assert state["period"] == {"start": "2025-11-19", "end": None}
    assert state["monitoring"] == "paused_harvest"


# ── woody crop without a registered planting (the parcel's own crop) ─────

def parcel_crop(species="Olea europaea", lifecycle=None, status="active", end=None):
    return {"species": species, "lifecycle": lifecycle, "status": status,
            "end": {"date": end.isoformat() if end else None, "provenance": "declared" if end else None}}


WOODY_WINDOW = Period(date(2025, 11, 19), None, "woody")


@pytest.mark.parametrize("previous", [
    None,
    cycle(lifecycle="unknown", species="Olea europaea"),  # no start at all
    cycle(lifecycle="unknown", species="Olea europaea", start=boundary(date(2015, 1, 1), "planned")),
])
def test_an_olive_without_a_planting_record_is_watched_all_year(previous):
    tl = {"current": None, "previous": previous, "parcel_crop": parcel_crop()}
    assert monitoring_period(tl, TODAY, False) == WOODY_WINDOW
    state = monitoring_state(tl, None, [], TODAY)
    assert state["crop"] == {"present": True, "species": "Olea europaea", "lifecycle": "perennial",
                             "planting_date": None, "termination_date": None}
    assert state["needs_activation"] is True and state["period"]["start"] == "2025-11-19"


def test_a_declared_perennial_without_a_planting_record_is_watched_too():
    tl = {"current": None, "previous": None, "parcel_crop": parcel_crop("Something odd", "perennial")}
    assert monitoring_period(tl, TODAY, False) == WOODY_WINDOW


@pytest.mark.parametrize("crop", [
    parcel_crop(status="terminated"),
    parcel_crop(status="uprooted"),
    parcel_crop(status="finished"),
    parcel_crop(end=date(2026, 5, 1)),
])
def test_a_terminated_woody_crop_is_not_present(crop):
    tl = {"current": None, "previous": None, "parcel_crop": crop}
    assert monitoring_period(tl, TODAY, False) is None
    state = monitoring_state(tl, None, [], TODAY)
    assert state["crop"]["present"] is False and state["needs_activation"] is False


def test_an_annual_without_a_registered_sowing_gets_no_fallback():
    planned = cycle(start=boundary(SOWN, "planned"))
    for crop in (parcel_crop("Triticum aestivum", "annual"), parcel_crop("Triticum aestivum", None),
                 parcel_crop("Zea mays", "unknown")):
        tl = {"current": None, "previous": planned, "parcel_crop": crop}
        assert monitoring_period(tl, TODAY, False) is None
        assert monitoring_state(tl, None, [], TODAY)["needs_activation"] is False


def test_the_fallback_is_ignored_when_the_timeline_has_a_registered_cycle():
    tl = {"current": sown(), "previous": None, "parcel_crop": parcel_crop()}
    assert monitoring_period(tl, TODAY, False) == Period(SOWN, None, "annual")


def test_the_fallback_does_not_serve_a_continuity_request():
    tl = {"current": None, "previous": None, "parcel_crop": parcel_crop("Zea mays", "annual")}
    assert monitoring_period(tl, TODAY, True) is None


@pytest.mark.parametrize("timeline_,expected", [
    (None, False),
    ({"current": None, "previous": None}, True),
    ({"current": None, "previous": cycle(start=boundary(SOWN, "planned"))}, True),
    ({"current": sown(), "previous": None}, False),
])
def test_needs_parcel_crop(timeline_, expected):
    assert needs_parcel_crop(timeline_) is expected


def test_utc_today_is_the_utc_date():
    from datetime import datetime, timezone
    assert abs((utc_today() - datetime.now(timezone.utc).date()).days) <= 1
