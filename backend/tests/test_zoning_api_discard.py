"""Discarding the pending proposal keeps the current zoning (real Postgres, fake broker)."""
from tests.test_zoning_api_accept import A, B, C, URL, _rows, _seed, api, as_roles  # noqa: F401  (fixture)
from tests.zoning_fakes import run


def _discard(client, run_id):
    return client.post(f"{URL}/discard", json={"run_id": run_id})


def test_discard_pending(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    r = _discard(client, B)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "discarded"
    rows = _rows(dbf)
    assert (rows[B].status, rows[B].outcome_reason) == ("discarded", "discarded_by_user")
    assert rows[A].status == "current"
    assert broker.created == broker.deleted == broker.appended == []


def test_discard_current_is_409(api):  # noqa: F811
    client, broker, dbf = api
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    assert _discard(client, A).status_code == 409
    rows = _rows(dbf)
    assert (rows[A].status, rows[B].status) == ("current", "pending")


def test_discard_unknown_is_404(api):  # noqa: F811
    client, broker, dbf = api
    _seed(dbf, current=A, accepted=True)
    assert _discard(client, C).status_code == 404


def test_farmer_cannot_discard(api):  # noqa: F811
    client, broker, dbf = api
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    as_roles(["Farmer"])
    assert _discard(client, B).status_code == 403
    assert _rows(dbf)[B].status == "pending"
