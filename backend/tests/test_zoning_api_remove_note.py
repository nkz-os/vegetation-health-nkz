"""Removing a zone's cause: gone from the broker and from the notes that would bring it back."""
from datetime import datetime, timezone
from urllib.parse import quote

from tests.test_zoning_api_accept import A, URL, _rows, _seed, api, as_roles  # noqa: F401  (fixture)
from tests.zoning_fakes import EAST, PARCEL, TENANT, WEST, run, run_neighbours


def _note_url(zone_id):
    return f"{URL}/zones/{quote(zone_id, safe='')}/note"


def _add_note(dbf, zone_id, geom, cause, minute):
    from app.models import ZoningNote
    db = dbf()
    db.add(ZoningNote(tenant_id=TENANT, parcel_id=PARCEL, zone_id=zone_id, geometry=geom, cause=cause,
                      created_by="u0", created_at=datetime(2026, 11, 10, 9, minute, tzinfo=timezone.utc)))
    db.commit()
    db.close()


def _causes(dbf):
    from app.models import ZoningNote
    return sorted(n.cause for n in dbf().query(ZoningNote).all())


def test_remove_cause(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    high = run(A)[0]["id"]
    assert client.post(_note_url(high), json={"cause": "waterlogging"}).status_code == 201
    r = client.delete(_note_url(high))
    assert r.status_code == 200, r.text
    assert r.json() == {"zone_id": high, "run_id": A, "cause": None}
    assert "technicianCause" not in broker.store[high]
    stored = {e["id"]: e for e in _rows(dbf)[A].entities}
    assert "technicianCause" not in stored[high]
    assert _causes(dbf) == []


def test_removed_cause_does_not_come_back(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run_neighbours(A)})
    _seed(dbf, current=A, accepted=True, current_entities=run_neighbours(A))
    _add_note(dbf, "urn:ngsi-ld:AgriManagementZone:p1:old:L1", WEST, "old west cause", 0)
    _add_note(dbf, run_neighbours(A)[1]["id"], EAST, "stones", 1)
    assert client.delete(_note_url(run_neighbours(A)[0]["id"])).status_code == 200
    assert _causes(dbf) == ["stones"]
    r = client.post(f"{URL}/merge", json={"run_id": A, "ranks": [1, 2], "target_rank": 2})
    assert r.status_code == 200, r.text
    causes = [e.get("technicianCause", {}).get("value") for e in broker.store.values()]
    assert causes == ["stones"]


def test_remove_cause_broker_failure(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    high = run(A)[0]["id"]
    assert client.post(_note_url(high), json={"cause": "waterlogging"}).status_code == 201
    broker.refuse_delete_attr = True
    assert client.delete(_note_url(high)).status_code == 502
    assert _causes(dbf) == ["waterlogging"]
    stored = {e["id"]: e for e in _rows(dbf)[A].entities}
    assert stored[high]["technicianCause"]["value"] == "waterlogging"


def test_remove_cause_unknown_zone_404(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    assert client.delete(_note_url("urn:ngsi-ld:AgriManagementZone:p1:zzzz:L9")).status_code == 404


def test_farmer_cannot_remove_cause(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    high = run(A)[0]["id"]
    assert client.post(_note_url(high), json={"cause": "waterlogging"}).status_code == 201
    as_roles(["Farmer"])
    assert client.delete(_note_url(high)).status_code == 403
    assert _causes(dbf) == ["waterlogging"]


def test_remove_cause_kept_by_broker_is_502(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    high = run(A)[0]["id"]
    assert client.post(_note_url(high), json={"cause": "waterlogging"}).status_code == 201
    broker.ignore_delete_attr = True
    assert client.delete(_note_url(high)).status_code == 502
    assert _causes(dbf) == ["waterlogging"]


def test_removed_cause_not_back_on_accepted_proposal(api):  # noqa: F811
    from tests.test_zoning_api_accept import B
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True, pendings=[(B, run(B), None)])
    high = run(A)[0]["id"]
    assert client.post(_note_url(high), json={"cause": "waterlogging"}).status_code == 201
    assert client.delete(_note_url(high)).status_code == 200
    assert client.post(f"{URL}/accept", json={"run_id": B}).status_code == 200
    assert all("technicianCause" not in e for e in broker.store.values())


def test_remove_cause_is_logged(api, caplog):  # noqa: F811
    import logging
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A, accepted=True)
    high = run(A)[0]["id"]
    assert client.post(_note_url(high), json={"cause": "waterlogging"}).status_code == 201
    with caplog.at_level(logging.INFO):
        assert client.delete(_note_url(high)).status_code == 200
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "cause removed" in msg and TENANT in msg and high in msg and "u-1" in msg and "notes_deleted=1" in msg
