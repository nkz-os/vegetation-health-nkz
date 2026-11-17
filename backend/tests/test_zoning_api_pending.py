"""Comparing the pending proposal with the current zoning (real Postgres, fake broker)."""
from urllib.parse import quote

from tests.test_zoning_api_accept import A, B, URL, _seed, api, as_roles  # noqa: F401  (fixture)
from tests.zoning_fakes import EAST, WEST, run, zone

NEW_DATES = ("2026-04-01", "2026-05-01", "2026-06-01")


def _note(client, zone_id, cause):
    r = client.post(f"{URL}/zones/{quote(zone_id, safe='')}/note", json={"cause": cause})
    assert r.status_code == 201, r.text


def test_pending_comparison(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    pend = [zone(B, 1, "high", WEST, area=1.2, rel=115, dates=NEW_DATES),
            zone(B, 3, "low", EAST, area=0.8, rel=86, dates=NEW_DATES)]
    _seed(dbf, current=A, accepted=True, pendings=[(B, pend, None)])
    _note(client, run(A)[0]["id"], "waterlogging")
    as_roles(["Farmer"])  # reading is open to every role
    r = client.get(f"{URL}/pending")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"]["run_id"] == A and body["current"]["accepted_at"] is not None
    assert body["current"]["source_dates"] == ["2026-04-01", "2026-05-01"]
    assert body["pending"]["run_id"] == B and body["pending"]["index_type"] == "NDVI"
    assert body["pending"]["source_dates"] == list(NEW_DATES)
    assert body["pending"]["new_dates"] == ["2026-06-01"]
    assert [lv["level"] for lv in body["pending"]["levels"]] == ["high", "low"]
    assert body["pending"]["levels"][0] == {"level": "high", "area_ha": 1.2, "relative_pct": 115}
    assert body["causes"] == [{"cause": "waterlogging", "from_level": "high", "to_level": "high"}]
    feats = body["geojson"]["features"]
    assert len(feats) == 2
    assert {f["properties"]["run_id"] for f in feats} == {B}
    assert all(f["properties"]["accepted"] is False for f in feats)


def test_cause_lost_in_proposal(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, [zone(B, 3, "low", EAST)], None)])
    _note(client, run(A)[0]["id"], "waterlogging")
    body = client.get(f"{URL}/pending").json()
    assert body["causes"] == [{"cause": "waterlogging", "from_level": "high", "to_level": None}]


def test_empty_pending(api):  # noqa: F811
    client, broker, dbf = api
    _seed(dbf, current=A, accepted=True, pendings=[(B, [], "too_few_dates")])
    r = client.get(f"{URL}/pending")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"]["levels"] == [] and body["geojson"]["features"] == []
    assert body["pending"]["outcome_reason"] == "too_few_dates"
    assert body["pending"]["new_dates"] == [] and body["causes"] == []


def test_no_pending_404(api):  # noqa: F811
    client, broker, dbf = api
    _seed(dbf, current=A, accepted=True)
    assert client.get(f"{URL}/pending").status_code == 404


def test_layer_flags_the_pending(api):  # noqa: F811
    from tests.zoning_fakes import PARCEL
    client, broker, dbf = api
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    layer = client.get(f"/api/vegetation/jobs/zoning/{PARCEL}/geojson")
    assert layer.status_code == 200 and layer.json()["has_pending"] is True
    client.post(f"{URL}/discard", json={"run_id": B})
    assert client.get(f"/api/vegetation/jobs/zoning/{PARCEL}/geojson").json()["has_pending"] is False


def test_single_date_as_scalar(api):  # noqa: F811
    client, broker, dbf = api
    one = zone(B, 1, "high", WEST)
    one["sourceDates"] = {"type": "Property", "value": "2026-06-01"}  # a one-item list compacted by the broker
    _seed(dbf, current=A, accepted=True, pendings=[(B, [one], None)])
    assert client.get(f"{URL}/pending").json()["pending"]["source_dates"] == ["2026-06-01"]
