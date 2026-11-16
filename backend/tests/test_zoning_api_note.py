"""Noting the cause of a zone of the current zoning (real Postgres, fake broker)."""
from urllib.parse import quote

from tests.test_zoning_api_accept import A, B, URL, _rows, _seed, api  # noqa: F401  (fixture)
from tests.zoning_fakes import EAST, PARCEL, TENANT, run


def _note_url(zone_id):
    return f"{URL}/zones/{quote(zone_id, safe='')}/note"


def test_note_saved_and_pushed(api):  # noqa: F811
    from app.models import ZoningNote
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    low_id = run(A)[1]["id"]
    r = client.post(_note_url(low_id), json={"cause": "  waterlogging in spring  "})
    assert r.status_code == 201, r.text
    assert broker.store[low_id]["technicianCause"] == {"type": "Property", "value": "waterlogging in spring"}
    notes = dbf().query(ZoningNote).all()
    assert [(n.tenant_id, n.parcel_id, n.zone_id, n.cause, n.created_by) for n in notes] == [
        (TENANT, PARCEL, low_id, "waterlogging in spring", "u-1")]
    assert notes[0].geometry == EAST
    stored = {e["id"]: e for e in _rows(dbf)[A].entities}
    assert stored[low_id]["technicianCause"]["value"] == "waterlogging in spring"


def test_note_on_unknown_zone_is_404(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    # A zone of the pending proposal is not in force: it cannot carry a note yet.
    assert client.post(_note_url(run(B)[1]["id"]), json={"cause": "stones"}).status_code == 404
    assert client.post(_note_url("urn:ngsi-ld:AgriManagementZone:p1:zzzzzzzz:L3"),
                       json={"cause": "stones"}).status_code == 404


def test_note_length_validated(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    low_id = run(A)[1]["id"]
    for bad in ("", "   ", "x" * 501):
        assert client.post(_note_url(low_id), json={"cause": bad}).status_code == 422, repr(bad)
    assert client.post(_note_url(low_id), json={"cause": "x" * 500}).status_code == 201
    assert not any(k == ["technicianCause"] for _, k in broker.appended[:-1])


def test_note_backfills_pre_p2_run(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})  # published before versions existed: no row
    low_id = run(A)[1]["id"]
    assert client.post(_note_url(low_id), json={"cause": "stones"}).status_code == 201
    row = _rows(dbf)[A]
    assert row.status == "current" and row.accepted_at is None


def test_note_broker_failure_saves_nothing(api):  # noqa: F811
    from app.models import ZoningNote
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    broker.refuse_append = True
    assert client.post(_note_url(run(A)[1]["id"]), json={"cause": "stones"}).status_code == 502
    assert dbf().query(ZoningNote).count() == 0
