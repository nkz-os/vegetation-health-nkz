"""Merging two levels through the API: a new accepted run replaces the current one (real Postgres, fake broker)."""
from tests.test_zoning_api_accept import A, B, URL, _rows, _seed, api  # noqa: F401  (fixture)
from tests.zoning_fakes import run


def _merge(client, run_id, ranks=(1, 3), target=3):
    return client.post(f"{URL}/merge", json={"run_id": run_id, "ranks": list(ranks), "target_rank": target})


def test_merge_publishes_new_accepted_run(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    r = _merge(client, A)
    assert r.status_code == 200, r.text
    new = r.json()["run_id"]
    assert new != A and broker.run_ids() == {new}
    (zone,) = broker.store.values()
    assert zone["zoneLevel"]["value"] == "low" and zone["derivedFromRunId"]["value"] == A
    rows = _rows(dbf)
    assert (rows[A].status, rows[new].status, rows[new].source, rows[new].derived_from) == (
        "superseded", "current", "merge", A)
    assert rows[new].accepted_by == "u-1" and rows[new].accepted_at is not None
    # A proposal parked on the old run can no longer be accepted: it is discarded.
    assert (rows[B].status, rows[B].outcome_reason) == ("discarded", "superseded_by_merge")


def test_merge_target_must_be_one_of_ranks(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    assert _merge(client, A, (1, 3), 2).status_code == 422
    assert _merge(client, A, (1, 1), 1).status_code == 422
    assert _merge(client, A, (1, 4), 1).status_code == 422  # level 4 is not in this zoning
    assert broker.run_ids() == {A}


def test_merge_non_current_is_409(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    assert _merge(client, B).status_code == 409
    assert broker.run_ids() == {A}


def test_merge_broker_failure_keeps_current(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    broker.refuse_create = True
    assert _merge(client, A).status_code == 502
    assert broker.run_ids() == {A}
    rows = _rows(dbf)
    assert list(rows) == [A] and rows[A].status == "current"


def test_map_layer_follows_the_merged_run(api):  # noqa: F811
    from tests.zoning_fakes import PARCEL
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    new = _merge(client, A).json()["run_id"]
    layer = client.get(f"/api/vegetation/jobs/zoning/{PARCEL}/geojson")
    assert layer.status_code == 200
    assert [f["properties"]["run_id"] for f in layer.json()["features"]] == [new]


def test_accept_pending_after_a_merge_is_409(api):  # noqa: F811
    """A pending proposal was parked against the run that a merge has since replaced."""
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    merged = _merge(client, A).json()["run_id"]
    r = client.post(f"{URL}/accept", json={"run_id": B})
    assert r.status_code == 409
    assert broker.run_ids() == {merged}
    assert _rows(dbf)[merged].status == "current"
