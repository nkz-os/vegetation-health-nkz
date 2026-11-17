"""Technician actions on a parcel's zoning: accept, note a cause, merge two levels.

Each action runs under the parcel's zoning lock and changes the broker first,
the tables last, so a broker failure leaves the tables as they were (the lock
block rolls back) and the previous current zoning in force.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.jobs.zoning_multidate import versions
from app.jobs.zoning_multidate.errors import (BrokerUnavailable, ZoningConflict, ZoningInvalid,
                                              ZoningNotFound)
from app.jobs.zoning_multidate.notes import assign_notes, notes_on_zone, with_causes
from app.jobs.zoning_multidate.publish import METHOD, ZONE_TYPE, publish, retire_quietly

_NKZ = "https://nkz-os.org/ns/"
_RANKS = (1, 2, 3, 4)


logger = logging.getLogger(__name__)


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


def _enqueue_backfill(tenant_id: str, parcel_id: str) -> None:
    """Ask for the zone products of the new current zoning; the zoning change never depends on it."""
    try:
        from app.tasks.processing_tasks import backfill_current_zone_products_task
        backfill_current_zone_products_task.delay(tenant_id, parcel_id)
    except Exception:
        logger.exception("zone backfill not queued tenant=%s parcel=%s", tenant_id, parcel_id)


def _append(fiware, entity_id: str, attrs: dict) -> None:
    if not fiware.append_attrs(entity_id, attrs):
        raise BrokerUnavailable(f"broker refused attributes on {entity_id}")


def accept(db, fiware, tenant_id: str, parcel_id: str, run_id: str, user_id: str, now: datetime):
    """Accept the current zoning (same ids) or publish the pending proposal as the accepted current one.

    The zone products are filled only when the current run changes.
    """
    version, changed = _accept(db, fiware, tenant_id, parcel_id, run_id, user_id, now)
    if changed:
        _enqueue_backfill(tenant_id, parcel_id)
    return version


def _accept(db, fiware, tenant_id: str, parcel_id: str, run_id: str, user_id: str, now: datetime):
    """``(version, changed)``: ``changed`` is True when a pending proposal became the current zoning."""
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
            retire_quietly(fiware, parcel_id, keep_run_id=cur.run_id)  # leftovers of a failed retire
            return cur, False

        pend = versions.pending(db, tenant_id, parcel_id)
        if pend is not None and pend.run_id == run_id:
            if pend.parked_on_run_id != (cur.run_id if cur is not None else None):
                raise ZoningConflict("the zoning changed since this proposal was made")
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
            retire_quietly(fiware, parcel_id, keep_run_id=run_id)
            pend.entities = entities
            versions.promote_pending(db, pend, accepted_by=user_id, accepted_at=now)
            return pend, True

        if versions.by_run(db, tenant_id, parcel_id, run_id) is not None:
            raise ZoningConflict("this zoning is no longer the current one nor the pending proposal")
        raise ZoningNotFound(run_id)


def discard(db, tenant_id: str, parcel_id: str, run_id: str, user_id: str):
    """Set the pending proposal aside; the current zoning and the broker are untouched."""
    with versions.parcel_lock(db, tenant_id, parcel_id):
        pend = versions.pending(db, tenant_id, parcel_id)
        if pend is not None and pend.run_id == run_id:
            versions.discard_pending(db, tenant_id, parcel_id, "discarded_by_user")
            logger.info("zoning proposal discarded tenant=%s parcel=%s run=%s user=%s",
                        tenant_id, parcel_id, run_id, user_id)
            return pend
        if versions.by_run(db, tenant_id, parcel_id, run_id) is not None:
            raise ZoningConflict("only the pending proposal can be discarded")
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


def _broker_cause(fiware, zone_id: str):
    """The cause the broker holds on a zone, whatever name form it comes back under."""
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
    return _value(_normalise(response.json()), "technicianCause")


def remove_note(db, fiware, tenant_id: str, parcel_id: str, zone_id: str, user_id: str):
    """Remove the cause of a zone of the current zoning, and every note that would bring it back."""
    with versions.parcel_lock(db, tenant_id, parcel_id):
        cur = versions.current(db, tenant_id, parcel_id)
        if cur is None:
            run_id = _run_of_zone(fiware, parcel_id, zone_id)
            if run_id is None or versions.by_run(db, tenant_id, parcel_id, run_id) is not None:
                raise ZoningNotFound(zone_id)
            cur = _backfill(db, fiware, tenant_id, parcel_id, run_id)
        if not any(e.get("id") == zone_id for e in cur.entities):
            raise ZoningNotFound(zone_id)
        try:
            removed = fiware.delete_attr(zone_id, "technicianCause")
        except Exception as exc:
            raise BrokerUnavailable(str(exc)) from exc
        # A 404 can also mean another expanded name: only a re-read proves the cause is gone.
        if not removed or _broker_cause(fiware, zone_id) is not None:
            raise BrokerUnavailable(f"broker kept technicianCause on {zone_id}")
        stale = notes_on_zone(versions.notes_of(db, tenant_id, parcel_id), cur.entities, zone_id)
        for note_row in stale:
            db.delete(note_row)
        logger.info("zoning cause removed tenant=%s parcel=%s zone=%s user=%s notes_deleted=%d",
                    tenant_id, parcel_id, zone_id, user_id, len(stale))
        cur.entities = [{k: v for k, v in copy.deepcopy(e).items() if k != "technicianCause"}
                        if e.get("id") == zone_id else copy.deepcopy(e) for e in cur.entities]
        db.flush()
    return {"zone_id": zone_id, "run_id": cur.run_id, "cause": None}


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
        retire_quietly(fiware, parcel_id, keep_run_id=new_run_id)
        version = versions.record_current(db, tenant_id, parcel_id, new_run_id, entities, source="merge",
                                          derived_from=run_id, index_type=cur.index_type,
                                          accepted_at=now, accepted_by=user_id)
    _enqueue_backfill(tenant_id, parcel_id)
    return version


def _levels(entities) -> list[dict]:
    ordered = sorted(entities or [], key=lambda e: _value(e, "zoneRank") or 0)
    return [{"level": _value(e, "zoneLevel"), "area_ha": _value(e, "areaHa"),
             "relative_pct": _value(e, "relativeVigorPct")} for e in ordered]


def summary(version) -> dict:
    return {
        "run_id": version.run_id,
        "status": version.status,
        "source": version.source,
        "derived_from": version.derived_from,
        "index_type": version.index_type,
        "outcome_reason": version.outcome_reason,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "accepted_at": version.accepted_at.isoformat() if version.accepted_at else None,
        "levels": _levels(version.entities),
    }


def _source_dates(entities) -> list[str]:
    dates: set[str] = set()
    for entity in entities or []:
        value = _value(entity, "sourceDates") or []
        dates.update([value] if isinstance(value, str) else value)  # one-item lists may come compacted
    return sorted(dates)


def pending_comparison(db, tenant_id: str, parcel_id: str) -> dict:
    """What the pending proposal changes against the current zoning, for the technician to decide."""
    from app.services.zoning_layer import zone_feature
    pend = versions.pending(db, tenant_id, parcel_id)
    if pend is None:
        raise ZoningNotFound(parcel_id)
    cur = versions.current(db, tenant_id, parcel_id)
    cur_entities = (cur.entities or []) if cur is not None else []
    pend_entities = pend.entities or []
    notes = versions.notes_of(db, tenant_id, parcel_id)

    def level_map(entities):  # cause -> level of the zone that holds it
        levels = {e.get("id"): _value(e, "zoneLevel") for e in entities}
        ranks = {e.get("id"): _value(e, "zoneRank") or 0 for e in entities}
        placed = sorted(assign_notes(notes, entities).items(), key=lambda kv: ranks.get(kv[0], 0))
        return {cause: levels.get(zid) for zid, cause in placed}

    before, after = level_map(cur_entities), level_map(pend_entities)
    causes = [{"cause": c, "from_level": before.get(c), "to_level": after.get(c)}
              for c in list(before) + [c for c in after if c not in before]]
    cur_dates, pend_dates = _source_dates(cur_entities), _source_dates(pend_entities)
    return {
        "current": None if cur is None else {
            "run_id": cur.run_id,
            "accepted_at": cur.accepted_at.isoformat() if cur.accepted_at else None,
            "source_dates": cur_dates,
            "levels": _levels(cur_entities),
        },
        "pending": {
            "run_id": pend.run_id,
            "created_at": pend.created_at.isoformat() if pend.created_at else None,
            "index_type": pend.index_type,
            "outcome_reason": pend.outcome_reason,
            "source_dates": pend_dates,
            "new_dates": [d for d in pend_dates if d not in set(cur_dates)],
            "levels": _levels(pend_entities),
        },
        "causes": causes,
        "geojson": {"type": "FeatureCollection",
                    "features": [zone_feature(pend.run_id, e, None)
                                 for e in sorted(pend_entities, key=lambda e: _value(e, "zoneRank") or 0)]},
    }
