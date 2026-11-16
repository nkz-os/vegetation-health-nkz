"""A new proposal never replaces a zoning the technician accepted (real Postgres, fake broker)."""
from datetime import datetime, timezone

from tests.test_zoning_md_run import (NOW, PARCEL_URN, TODAY, _fiware, _row, _sources,
                                      _tif)

T = "acme"
ACCEPTED = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
OLD = {"id": "urn:ngsi-ld:AgriManagementZone:acme-p1:aaaaaaaa:L1", "zoningMethod": "vegetationIndexMultiDate",
       "zoningRunId": "aaaaaaaa", "hasAgriParcel": PARCEL_URN}


def _store(db):
    from app.jobs.zoning_multidate.run import VersionStore
    return VersionStore(db=db, tenant_id=T, job_id="job-1")


def _seed_current(db, accepted):
    from app.jobs.zoning_multidate import versions as v
    with v.parcel_lock(db, T, PARCEL_URN):
        v.record_current(db, T, PARCEL_URN, "aaaaaaaa", [dict(OLD)], source="proposal", index_type="NDVI",
                         accepted_at=ACCEPTED if accepted else None, accepted_by="u1" if accepted else None)


def _zoned():
    rows = [_row(i) for i in range(4)]
    return _sources(rows, {r.job_id: _tif(0.6) for r in rows})


def _flat():
    rows = [_row(i) for i in range(4)]
    return _sources(rows, {r.job_id: _tif(0.6, split=False) for r in rows})


def test_accepted_current_is_not_replaced(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.jobs.zoning_multidate.run import propose_zoning, vra_job_result
    db = zoning_db()
    _seed_current(db, accepted=True)
    fw = _fiware([OLD])
    res = propose_zoning(_zoned(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert res.status == "zones" and res.pending_acceptance is True
    fw.create_entity.assert_not_called()
    fw.delete_entity.assert_not_called()
    assert v.current(db, T, PARCEL_URN).run_id == "aaaaaaaa"
    pend = v.pending(db, T, PARCEL_URN)
    assert pend.run_id == res.run_id and len(pend.entities) == len(res.zones) and pend.job_id == "job-1"
    assert vra_job_result(res)["pending_acceptance"] is True


def test_accepted_current_survives_no_zones(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.jobs.zoning_multidate.run import propose_zoning
    db = zoning_db()
    _seed_current(db, accepted=True)
    fw = _fiware([OLD])
    res = propose_zoning(_flat(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert (res.status, res.reason, res.pending_acceptance) == ("no_zones", "homogeneous", True)
    fw.delete_entity.assert_not_called()
    assert v.current(db, T, PARCEL_URN).run_id == "aaaaaaaa"
    pend = v.pending(db, T, PARCEL_URN)
    assert (pend.entities, pend.outcome_reason) == ([], "homogeneous")


def test_unaccepted_current_is_replaced_as_today(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.jobs.zoning_multidate.run import propose_zoning
    db = zoning_db()
    _seed_current(db, accepted=False)
    fw = _fiware([OLD])
    res = propose_zoning(_zoned(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert res.status == "zones" and res.pending_acceptance is False
    assert fw.create_entity.call_count == len(res.zones)
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == [OLD["id"]]
    cur = v.current(db, T, PARCEL_URN)
    assert cur.run_id == res.run_id and cur.accepted_at is None and cur.source == "proposal"
    assert v.by_run(db, T, PARCEL_URN, "aaaaaaaa").status == "superseded"


def test_unaccepted_current_leaves_with_no_zones(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.jobs.zoning_multidate.run import propose_zoning
    db = zoning_db()
    _seed_current(db, accepted=False)
    fw = _fiware([OLD])
    res = propose_zoning(_flat(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert (res.status, res.pending_acceptance) == ("no_zones", False)
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == [OLD["id"]]
    assert v.current(db, T, PARCEL_URN) is None and v.pending(db, T, PARCEL_URN) is None


def test_published_proposal_carries_notes_by_location(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.jobs.zoning_multidate.run import propose_zoning
    from app.models import ZoningNote
    db = zoning_db()
    first = propose_zoning(_zoned(), _fiware(), PARCEL_URN, None, TODAY, NOW, store=_store(db))
    low = next(e for e in v.current(db, T, PARCEL_URN).entities if e["zoneLevel"]["value"] == "low")
    db.add(ZoningNote(tenant_id=T, parcel_id=PARCEL_URN, zone_id=low["id"],
                      geometry=low["location"]["value"], cause="waterlogging", created_by="u1"))
    db.commit()
    fw = _fiware()
    second = propose_zoning(_zoned(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert second.run_id != first.run_id
    published = {c.args[0]["zoneLevel"]["value"]: c.args[0] for c in fw.create_entity.call_args_list}
    assert published["low"]["technicianCause"]["value"] == "waterlogging"
    assert "technicianCause" not in published["high"]
    stored = {e["zoneLevel"]["value"]: e for e in v.current(db, T, PARCEL_URN).entities}
    assert stored["low"]["technicianCause"]["value"] == "waterlogging"


def test_sources_unavailable_records_nothing(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.jobs.zoning_multidate.run import propose_zoning
    db = zoning_db()
    _seed_current(db, accepted=False)
    rows = [_row(i) for i in range(4)]
    res = propose_zoning(_sources(rows, {}), _fiware([OLD]), PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert res.reason == "sources_unavailable"
    assert v.current(db, T, PARCEL_URN).run_id == "aaaaaaaa" and v.pending(db, T, PARCEL_URN) is None


LEFTOVER = {"id": "urn:ngsi-ld:AgriManagementZone:acme-p1:zzzzzzzz:L3", "zoningMethod": "vegetationIndexMultiDate",
            "zoningRunId": "zzzzzzzz", "hasAgriParcel": PARCEL_URN}


def test_kept_pending_still_sweeps_leftovers(zoning_db):
    """Zones an earlier failed retire left behind go, even though the accepted zoning stays."""
    from app.jobs.zoning_multidate.run import propose_zoning
    db = zoning_db()
    _seed_current(db, accepted=True)
    fw = _fiware([OLD, LEFTOVER])
    res = propose_zoning(_zoned(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert res.pending_acceptance is True
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == [LEFTOVER["id"]]


def test_no_zones_with_accepted_current_sweeps_leftovers(zoning_db):
    from app.jobs.zoning_multidate.run import propose_zoning
    db = zoning_db()
    _seed_current(db, accepted=True)
    fw = _fiware([OLD, LEFTOVER])
    propose_zoning(_flat(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == [LEFTOVER["id"]]


def test_failed_retire_on_no_zones_still_withdraws_the_current(zoning_db):
    from app.jobs.zoning_multidate import versions as v
    from app.jobs.zoning_multidate.run import propose_zoning
    db = zoning_db()
    _seed_current(db, accepted=False)
    fw = _fiware([OLD])
    fw.delete_entity.side_effect = RuntimeError("broker 500")
    res = propose_zoning(_flat(), fw, PARCEL_URN, None, TODAY, NOW, store=_store(db))
    assert res.status == "no_zones"
    assert v.current(db, T, PARCEL_URN) is None
