"""Technician actions on a parcel's zoning: accept, note a cause, merge two levels.

Each action runs under the parcel's zoning lock and changes the broker first,
the tables last, so a broker failure leaves the tables as they were (the lock
block rolls back) and the previous current zoning in force.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.jobs.zoning_multidate import versions
from app.jobs.zoning_multidate.notes import assign_notes, with_causes
from app.jobs.zoning_multidate.publish import METHOD, ZONE_TYPE, publish, retire_after_publish

_NKZ = "https://nkz-os.org/ns/"
_RANKS = (1, 2, 3, 4)


class ZoningNotFound(Exception):
    pass


class ZoningConflict(Exception):
    pass


class BrokerUnavailable(Exception):
    pass


class ZoningInvalid(Exception):
    pass


def _stamp(at: datetime) -> dict:
    return {"type": "Property", "value": {"@type": "DateTime", "@value": at.strftime("%Y-%m-%dT%H:%M:%SZ")}}


def _value(entity: dict, name: str) -> Any:
    v = entity.get(name)
    if isinstance(v, dict):
        return v.get("object", v.get("value"))
    return v


def _normalise(entity: dict) -> dict:
    """An entity as read from the broker, in the shape the proposal publishes it."""
    out: dict = {}
    for key, value in entity.items():
        if key in ("@context", "createdAt", "modifiedAt"):
            continue
        if key.startswith(_NKZ):
            key = key[len(_NKZ):]
        elif key.startswith("nkz:"):
            key = key[4:]
        if key == "refAgriParcel":  # same IRI as hasAgriParcel, compacted under the legacy term
            key = "hasAgriParcel"
        out[key] = value
    return out


def _fetch_run(fiware, parcel_id: str, run_id: str) -> list[dict]:
    """The zones of a multi-date run, read by their deterministic ids."""
    short = parcel_id.rsplit(":", 1)[-1]
    found = []
    for rank in _RANKS:
        eid = f"urn:ngsi-ld:{ZONE_TYPE}:{short}:{run_id[:8]}:L{rank}"
        response = fiware.session.get(
            f"{fiware.context_broker_url}/ngsi-ld/v1/entities/{quote(eid, safe='')}",
            headers={"Accept": "application/json", "Content-Type": None},
            timeout=10,
        )
        if response.status_code == 404:
            continue
        try:
            response.raise_for_status()
        except Exception as exc:
            raise BrokerUnavailable(str(exc)) from exc
        entity = _normalise(response.json())
        if (_value(entity, "zoningRunId") == run_id and _value(entity, "hasAgriParcel") == parcel_id
                and _value(entity, "zoningMethod") == METHOD):
            found.append(entity)
    return found


def _backfill(db, fiware, tenant_id: str, parcel_id: str, run_id: str):
    """A run published before versions existed: record it as the (unaccepted) current one."""
    entities = _fetch_run(fiware, parcel_id, run_id)
    if not entities:
        raise ZoningNotFound(run_id)
    return versions.record_current(db, tenant_id, parcel_id, run_id, entities, source="proposal",
                                   index_type=_value(entities[0], "variableAttribute"))


def _append(fiware, entity_id: str, attrs: dict) -> None:
    if not fiware.append_attrs(entity_id, attrs):
        raise BrokerUnavailable(f"broker refused attributes on {entity_id}")


def accept(db, fiware, tenant_id: str, parcel_id: str, run_id: str, user_id: str, now: datetime):
    """Accept the current zoning (same ids) or publish the pending proposal as the accepted current one."""
    with versions.parcel_lock(db, tenant_id, parcel_id):
        cur = versions.current(db, tenant_id, parcel_id)
        if cur is None and versions.by_run(db, tenant_id, parcel_id, run_id) is None:
            cur = _backfill(db, fiware, tenant_id, parcel_id, run_id)

        if cur is not None and cur.run_id == run_id:
            if cur.accepted_at is None:
                stamp = _stamp(now)
                for entity in cur.entities:
                    _append(fiware, entity["id"], {"acceptedAt": stamp})
                cur.entities = [{**copy.deepcopy(e), "acceptedAt": stamp} for e in cur.entities]
                versions.mark_accepted(db, cur, user_id, now)
            return cur

        pend = versions.pending(db, tenant_id, parcel_id)
        if pend is not None and pend.run_id == run_id:
            if not pend.entities:
                raise ZoningConflict("the pending proposal has no zones to publish")
            stamp = _stamp(now)
            entities = with_causes(pend.entities,
                                   assign_notes(versions.notes_of(db, tenant_id, parcel_id), pend.entities))
            entities = [{**e, "acceptedAt": stamp} for e in entities]
            try:
                publish(fiware, entities)
            except RuntimeError as exc:
                raise BrokerUnavailable(str(exc)) from exc
            retire_after_publish(fiware, parcel_id, run_id)
            pend.entities = entities
            versions.promote_pending(db, pend, accepted_by=user_id, accepted_at=now)
            return pend

        if versions.by_run(db, tenant_id, parcel_id, run_id) is not None:
            raise ZoningConflict("this zoning is no longer the current one nor the pending proposal")
        raise ZoningNotFound(run_id)


def _run_of_zone(fiware, parcel_id: str, zone_id: str) -> str | None:
    response = fiware.session.get(
        f"{fiware.context_broker_url}/ngsi-ld/v1/entities/{quote(zone_id, safe='')}",
        headers={"Accept": "application/json", "Content-Type": None},
        timeout=10,
    )
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except Exception as exc:
        raise BrokerUnavailable(str(exc)) from exc
    entity = _normalise(response.json())
    if _value(entity, "hasAgriParcel") != parcel_id or _value(entity, "zoningMethod") != METHOD:
        return None
    return _value(entity, "zoningRunId")


def note(db, fiware, tenant_id: str, parcel_id: str, zone_id: str, cause: str, user_id: str):
    """Note the cause of a zone of the current zoning; the note keeps the zone's geometry."""
    from app.models.zoning import ZoningNote
    with versions.parcel_lock(db, tenant_id, parcel_id):
        cur = versions.current(db, tenant_id, parcel_id)
        if cur is None:
            run_id = _run_of_zone(fiware, parcel_id, zone_id)
            if run_id is None or versions.by_run(db, tenant_id, parcel_id, run_id) is not None:
                raise ZoningNotFound(zone_id)
            cur = _backfill(db, fiware, tenant_id, parcel_id, run_id)
        zone = next((e for e in cur.entities if e.get("id") == zone_id), None)
        if zone is None:
            raise ZoningNotFound(zone_id)
        attr = {"type": "Property", "value": cause}
        _append(fiware, zone_id, {"technicianCause": attr})
        db.add(ZoningNote(tenant_id=tenant_id, parcel_id=parcel_id, zone_id=zone_id,
                          geometry=zone["location"]["value"], cause=cause, created_by=user_id))
        cur.entities = [{**copy.deepcopy(e), "technicianCause": attr} if e.get("id") == zone_id
                        else copy.deepcopy(e) for e in cur.entities]
        db.flush()
    return {"zone_id": zone_id, "run_id": cur.run_id, "cause": cause}


def merge(db, fiware, tenant_id: str, parcel_id: str, run_id: str, ranks: tuple[int, int],
          target_rank: int, user_id: str, now: datetime):
    """Publish a new accepted run where two levels of the current zoning are one."""
    import uuid

    from app.jobs.zoning_multidate.merge import merge_levels
    with versions.parcel_lock(db, tenant_id, parcel_id):
        cur = versions.current(db, tenant_id, parcel_id)
        if cur is None and versions.by_run(db, tenant_id, parcel_id, run_id) is None:
            cur = _backfill(db, fiware, tenant_id, parcel_id, run_id)
        if cur is None or cur.run_id != run_id:
            raise ZoningConflict("only the current zoning can be edited")
        new_run_id = uuid.uuid4().hex
        try:
            entities = merge_levels(cur.entities, ranks, target_rank, new_run_id, run_id, now)
        except ValueError as exc:
            raise ZoningInvalid(str(exc)) from exc
        entities = with_causes(entities, assign_notes(versions.notes_of(db, tenant_id, parcel_id), entities))
        try:
            publish(fiware, entities)
        except RuntimeError as exc:
            raise BrokerUnavailable(str(exc)) from exc
        retire_after_publish(fiware, parcel_id, new_run_id)
        version = versions.record_current(db, tenant_id, parcel_id, new_run_id, entities, source="merge",
                                          derived_from=run_id, index_type=cur.index_type,
                                          accepted_at=now, accepted_by=user_id)
    return version


def summary(version) -> dict:
    levels = sorted(
        ({"rank": _value(e, "zoneRank"), "level": _value(e, "zoneLevel"),
          "area_ha": _value(e, "areaHa"), "relative_pct": _value(e, "relativeVigorPct")}
         for e in version.entities or []),
        key=lambda z: z["rank"] or 0,
    )
    return {
        "run_id": version.run_id,
        "status": version.status,
        "source": version.source,
        "derived_from": version.derived_from,
        "index_type": version.index_type,
        "outcome_reason": version.outcome_reason,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "accepted_at": version.accepted_at.isoformat() if version.accepted_at else None,
        "levels": [{k: z[k] for k in ("level", "area_ha", "relative_pct")} for z in levels],
    }
