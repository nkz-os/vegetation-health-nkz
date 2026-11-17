"""Zoning versions of a parcel: accept, note a cause, merge levels, list versions.

Plain ``def`` handlers: the broker client is blocking, so FastAPI runs them in its thread pool.
The geometry stack (shapely, pyproj) is imported by the handlers, not when the app starts.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.jobs.zoning_multidate import errors, versions
from app.middleware.auth import require_auth

router = APIRouter(prefix="/api/vegetation/zoning", tags=["zoning"])
logger = logging.getLogger(__name__)


# Roles that may change a zoning; field workers (Farmer) only read it.
EDITOR_ROLES = frozenset({"TechnicalConsultant", "TenantAdmin", "PlatformAdmin"})


def require_editor(current_user: dict) -> None:
    if not EDITOR_ROLES.intersection(current_user.get("roles") or []):
        raise HTTPException(status_code=403, detail="Not allowed to edit zonings")


def broker_for(tenant_id: str):
    from app.services.fiware_integration import FIWAREClient
    return FIWAREClient(os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026"),
                        tenant_id=tenant_id)


def _run(action):
    try:
        return action()
    except errors.ZoningNotFound as exc:
        raise HTTPException(status_code=404, detail="Zoning not found") from exc
    except errors.ZoningInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except errors.ZoningConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except errors.BrokerUnavailable as exc:
        logger.error("zoning action failed at the broker: %s", exc)
        raise HTTPException(status_code=502, detail="Context broker unavailable; nothing was changed") from exc


class AcceptRequest(BaseModel):
    run_id: str = Field(min_length=8, max_length=64)


@router.post("/{parcel_id}/accept")
def accept_zoning(parcel_id: str, body: AcceptRequest,
                  current_user: dict = Depends(require_auth),
                  db: Session = Depends(get_db_with_tenant)):
    require_editor(current_user)
    from app.jobs.zoning_multidate import actions
    tenant = current_user["tenant_id"]
    version = _run(lambda: actions.accept(db, broker_for(tenant), tenant, parcel_id, body.run_id,
                                          current_user["user_id"], datetime.now(timezone.utc)))
    return actions.summary(version)


@router.post("/{parcel_id}/discard")
def discard_proposal(parcel_id: str, body: AcceptRequest,
                     current_user: dict = Depends(require_auth),
                     db: Session = Depends(get_db_with_tenant)):
    require_editor(current_user)
    from app.jobs.zoning_multidate import actions
    version = _run(lambda: actions.discard(db, current_user["tenant_id"], parcel_id, body.run_id,
                                           current_user["user_id"]))
    return actions.summary(version)


class NoteRequest(BaseModel):
    # Free text written by the technician: user data, stored as given (trimmed).
    cause: str = Field(min_length=1, max_length=500)

    @field_validator("cause", mode="before")
    @classmethod
    def _trim(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/{parcel_id}/zones/{zone_id}/note", status_code=201)
def note_zone(parcel_id: str, zone_id: str, body: NoteRequest,
              current_user: dict = Depends(require_auth),
              db: Session = Depends(get_db_with_tenant)):
    require_editor(current_user)
    from app.jobs.zoning_multidate import actions
    tenant = current_user["tenant_id"]
    return _run(lambda: actions.note(db, broker_for(tenant), tenant, parcel_id, zone_id, body.cause,
                                     current_user["user_id"]))


@router.delete("/{parcel_id}/zones/{zone_id}/note")
def remove_zone_note(parcel_id: str, zone_id: str,
                     current_user: dict = Depends(require_auth),
                     db: Session = Depends(get_db_with_tenant)):
    require_editor(current_user)
    from app.jobs.zoning_multidate import actions
    tenant = current_user["tenant_id"]
    return _run(lambda: actions.remove_note(db, broker_for(tenant), tenant, parcel_id, zone_id,
                                            current_user["user_id"]))


class MergeRequest(BaseModel):
    run_id: str = Field(min_length=8, max_length=64)
    ranks: list[int] = Field(min_length=2, max_length=2)
    target_rank: int = Field(ge=1, le=4)


@router.post("/{parcel_id}/merge")
def merge_levels(parcel_id: str, body: MergeRequest,
                 current_user: dict = Depends(require_auth),
                 db: Session = Depends(get_db_with_tenant)):
    require_editor(current_user)
    from app.jobs.zoning_multidate import actions
    tenant = current_user["tenant_id"]
    a, b = body.ranks
    version = _run(lambda: actions.merge(db, broker_for(tenant), tenant, parcel_id, body.run_id, (a, b),
                                         body.target_rank, current_user["user_id"],
                                         datetime.now(timezone.utc)))
    return actions.summary(version)


@router.get("/{parcel_id}/versions")
def list_versions(parcel_id: str,
                  current_user: dict = Depends(require_auth),
                  db: Session = Depends(get_db_with_tenant)):
    from app.jobs.zoning_multidate import actions
    return [actions.summary(v) for v in versions.history(db, current_user["tenant_id"], parcel_id)]


@router.get("/{parcel_id}/pending")
def pending_proposal(parcel_id: str,
                     current_user: dict = Depends(require_auth),
                     db: Session = Depends(get_db_with_tenant)):
    from app.jobs.zoning_multidate import actions
    return _run(lambda: actions.pending_comparison(db, current_user["tenant_id"], parcel_id))
