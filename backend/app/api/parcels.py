"""
Parcels API — overview + detail endpoints for the redesigned module page.

Replaces the scattered /entities/{id}/data-status + /results + /crop-seasons +
quota fetches with a single per-parcel overview that the new UI consumes in
one call. Also owns the hard-delete cascade for jobs.
"""

import logging
import os
import uuid as uuid_mod
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from app.api.scenes import _dispatch_analyze_for_parcel, _resolve_custom_formula_specs
from app.celery_app import celery_app
from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import (
    VegetationCropSeason,
    VegetationIndexCache,
    VegetationJob,
    VegetationScene,
)
from app.services.storage import create_storage_service, generate_tenant_bucket_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/parcels", tags=["parcels"])


# ── Serialisation helpers ───────────────────────────────────────────────

def _job_card(job: VegetationJob) -> Dict[str, Any]:
    """Compact job representation for the parcel detail UI."""
    result = job.result or {}
    # Two skip flags coexist: download-side scenes set 'skipped_due_to_clouds'
    # when the SCL micro-validation rejects the scene; calc_index workers set
    # 'skipped' when the index could not be produced (e.g. all-NaN raster
    # after geometry mask). Either flag means the job is 'completed' but
    # produced no usable artefact.
    skipped = bool(result.get("skipped_due_to_clouds") or result.get("skipped"))
    indices = result.get("calculate_indices") or job.parameters.get("calculate_indices")
    if not indices and result.get("index_type"):
        indices = [result.get("index_type")]
    return {
        "id": str(job.id),
        "type": job.job_type,
        "status": "skipped" if (job.status == "completed" and skipped) else job.status,
        "indices": indices or [],
        "scene_product_id": result.get("scene_product_id") or job.parameters.get("scene_id"),
        "sensing_date": result.get("sensing_date"),
        "local_cloud_pct": result.get("local_cloud_pct"),
        "local_cloud_threshold": result.get("local_cloud_threshold"),
        "raster_path": result.get("raster_path"),
        "index_type": result.get("index_type"),
        "stats_mean": (result.get("statistics") or {}).get("mean"),
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_by": job.created_by,
        "crop_season_id": str(job.crop_season_id) if job.crop_season_id else None,
    }


def _season_card(
    season: VegetationCropSeason, jobs: List[VegetationJob]
) -> Dict[str, Any]:
    return {
        "id": str(season.id),
        "crop_type": season.crop_type,
        "label": season.label,
        "start_date": season.start_date.isoformat() if season.start_date else None,
        "end_date": season.end_date.isoformat() if season.end_date else None,
        "is_active": bool(season.is_active),
        "monitoring_enabled": bool(season.monitoring_enabled),
        "jobs": [_job_card(j) for j in jobs],
        "stats": {
            "jobs_total": len(jobs),
            "jobs_completed": sum(1 for j in jobs if j.status == "completed"),
            "jobs_failed": sum(1 for j in jobs if j.status == "failed"),
            "jobs_skipped": sum(
                1
                for j in jobs
                if j.status == "completed"
                and (j.result or {}).get("skipped_due_to_clouds")
            ),
        },
    }


async def _resolve_parcel_meta(entity_id: str, tenant_id: str) -> Dict[str, Any]:
    """Best-effort fetch of parcel name + geometry from Orion-LD.

    Tolerant: returns partial info on 404 / network issues so the page still
    paints something. Uses Link header with the schema.org context so the
    `name` attribute (canonicalised to https://schema.org/name in SDM) is
    actually returned.
    """
    orion_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
    context_url = os.getenv(
        "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
    )
    headers = {
        "Accept": "application/json",
        "NGSILD-Tenant": tenant_id,
        "Link": f'<{context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
    }
    out: Dict[str, Any] = {"entity_id": entity_id, "name": None, "location": None}
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            resp = await client.get(
                f"{orion_url}/ngsi-ld/v1/entities/{entity_id}", headers=headers
            )
            if resp.status_code == 200:
                ent = resp.json()
                # name may live under any of several keys depending on context expansion
                for key in ("name", "https://schema.org/name"):
                    val = ent.get(key)
                    if isinstance(val, dict):
                        out["name"] = val.get("value")
                        break
                    if isinstance(val, str):
                        out["name"] = val
                        break
                loc = ent.get("location")
                if isinstance(loc, dict):
                    out["location"] = loc.get("value") or loc
    except Exception as exc:
        logger.debug("Could not resolve parcel metadata for %s: %s", entity_id, exc)
    return out


# ── Endpoint ────────────────────────────────────────────────────────────


@router.get("/{entity_id}/overview")
async def get_parcel_overview(
    entity_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Single round-trip payload for the parcel detail page.

    Returns parcel metadata + all crop seasons (active and historical) with
    their jobs grouped under each, plus a `legacy_jobs` bucket for any rows
    without a season FK (jobs created before migration 008).
    """
    tenant_id = current_user["tenant_id"]

    # All crop seasons for this parcel (active + soft-deleted = excluded)
    seasons: List[VegetationCropSeason] = (
        db.query(VegetationCropSeason)
        .filter(
            VegetationCropSeason.tenant_id == tenant_id,
            VegetationCropSeason.entity_id == entity_id,
            VegetationCropSeason.deleted_at.is_(None),
        )
        .order_by(VegetationCropSeason.start_date.desc())
        .all()
    )

    # All non-deleted jobs for the parcel; we then bucket them in Python
    jobs: List[VegetationJob] = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.deleted_at.is_(None),
        )
        .order_by(desc(VegetationJob.created_at))
        .limit(500)  # safety cap; UI paginates client-side per season for now
        .all()
    )

    season_jobs: Dict[str, List[VegetationJob]] = {}
    legacy_jobs: List[VegetationJob] = []
    for j in jobs:
        if j.crop_season_id is None:
            legacy_jobs.append(j)
        else:
            season_jobs.setdefault(str(j.crop_season_id), []).append(j)

    season_payload = [_season_card(s, season_jobs.get(str(s.id), [])) for s in seasons]

    # Current state: latest completed calc_index across the whole parcel
    # Latest "real" calc_index — must have a raster_path. A calc_index can
    # complete with skipped=true (all-NaN, mask collision, etc.) and that
    # would otherwise mislead the UI into showing 'NDRE / —' as current
    # state when really there is no usable raster for that index yet.
    latest = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
            VegetationJob.result["raster_path"].astext.isnot(None),
        )
        .order_by(desc(VegetationJob.completed_at))
        .first()
    )
    current_state = None
    if latest and latest.result:
        current_state = {
            "index_type": latest.result.get("index_type"),
            "sensing_date": latest.result.get("sensing_date"),
            "stats_mean": (latest.result.get("statistics") or {}).get("mean"),
            "raster_path": latest.result.get("raster_path"),
            "job_id": str(latest.id),
        }

    # Available indices = those that have at least one completed calc_index
    # with a real raster_path. The vegetation_indices_cache table is dead in
    # the FIWARE-canonical pipeline (Orion-LD is the source of truth, no
    # direct writes from the worker), so we read from vegetation_jobs.result
    # instead.
    available_indices = [
        r[0]
        for r in (
            db.query(func.distinct(VegetationJob.result["index_type"].astext))
            .filter(
                VegetationJob.tenant_id == tenant_id,
                VegetationJob.entity_id == entity_id,
                VegetationJob.job_type == "calculate_index",
                VegetationJob.status == "completed",
                VegetationJob.deleted_at.is_(None),
                VegetationJob.result["raster_path"].astext.isnot(None),
            )
            .all()
        )
        if r[0]
    ]

    # Recent skips so the UI can explain "no layer" emptiness
    skip_jobs = [j for j in jobs if j.job_type == "download"
                 and j.status == "completed"
                 and (j.result or {}).get("skipped_due_to_clouds")][:10]
    recent_skips = [
        {
            "job_id": str(j.id),
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            "scene_id": (j.result or {}).get("scene_id"),
            "sensing_date": (j.result or {}).get("sensing_date"),
            "local_cloud_pct": (j.result or {}).get("local_cloud_pct"),
            "local_cloud_threshold": (j.result or {}).get("local_cloud_threshold"),
            "message": (j.result or {}).get("message"),
        }
        for j in skip_jobs
    ]

    # Active background work
    active_jobs_count = sum(1 for j in jobs if j.status in ("pending", "running"))

    parcel_meta = await _resolve_parcel_meta(entity_id, tenant_id)

    return {
        "parcel": parcel_meta,
        "seasons": season_payload,
        "legacy_jobs": [_job_card(j) for j in legacy_jobs],
        "current_state": current_state,
        "available_indices": available_indices,
        "recent_skips": recent_skips,
        "active_jobs_count": active_jobs_count,
    }


# ── Hard delete cascade ────────────────────────────────────────────────


def _delete_raster_safely(raster_path: str, tenant_id: str) -> None:
    """Best-effort delete of a raster file from MinIO. NoSuchKey is fine."""
    try:
        bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(tenant_id)
        storage = create_storage_service(
            storage_type=os.getenv("STORAGE_TYPE", "s3"),
            default_bucket=bucket,
        )
        storage.delete_file(raster_path, bucket)
    except Exception as exc:
        # NoSuchKey, transient S3 issue, missing creds — do not break the
        # transactional cleanup; the file is either gone or will be GC'd later.
        logger.debug("Raster delete failed for %s: %s", raster_path, exc)


async def _delete_orion_entity_if_orphan(
    entity_id: str, tenant_id: str, db: Session
) -> None:
    """Drop the per-parcel VegetationIndex entity from Orion-LD ONLY when no
    completed calc_index jobs remain for this parcel.

    Per fiware_integration._entity_id_for_parcel, there is exactly one
    VegetationIndex entity per parcel; it is PATCH-updated on each analysis.
    We only DELETE it when the parcel has truly nothing left to surface,
    otherwise we'd lie about the entity having been wiped.
    """
    remaining = (
        db.query(func.count(VegetationJob.id))
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
        )
        .scalar()
    ) or 0
    if remaining > 0:
        return

    parcel_short = entity_id.split(":")[-1] if ":" in entity_id else entity_id
    veg_entity_id = f"urn:ngsi-ld:VegetationIndex:{tenant_id}:{parcel_short}"
    orion_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
    headers = {"Accept": "application/json", "NGSILD-Tenant": tenant_id}
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            resp = await client.delete(
                f"{orion_url}/ngsi-ld/v1/entities/{veg_entity_id}", headers=headers
            )
            if resp.status_code in (204, 404):
                logger.info(
                    "Orion-LD VegetationIndex %s: %s (parcel cleaned)",
                    veg_entity_id, resp.status_code,
                )
            else:
                logger.warning(
                    "Orion-LD entity DELETE returned %s for %s: %s",
                    resp.status_code, veg_entity_id, resp.text[:200],
                )
    except Exception as exc:
        logger.warning("Orion-LD DELETE %s failed: %s", veg_entity_id, exc)


def _hard_delete_one(db: Session, tenant_id: str, job: VegetationJob) -> int:
    """Cascade-delete a single job + its raster + cache. For download jobs,
    recurse into calc_index children that share the same scene_id. Returns
    the number of rows deleted (counting the recursion).

    Idempotent: if the raster or row is already gone, that's fine.
    """
    # 1. Cancel Celery if the task is still in-flight.
    if job.status in ("pending", "running") and job.celery_task_id:
        try:
            celery_app.control.revoke(job.celery_task_id, terminate=True)
        except Exception as exc:
            logger.debug("Celery revoke failed for %s: %s", job.celery_task_id, exc)

    deleted = 1

    # 2. Cascade for download jobs: each download produces N calc_index
    #    children that reference the same scene UUID in their result.
    if job.job_type == "download":
        scene_uuid = (job.result or {}).get("scene_id")
        if scene_uuid:
            # Filter children directly in the JSONB columns instead of
            # bringing every calculate_index row for this parcel into
            # memory. Two predicates cover both the canonical scene UUID
            # in result.scene_id (post-success rows) and the scene UUID
            # already pinned in parameters.scene_id at enqueue time
            # (pre-success rows from the race window).
            children = (
                db.query(VegetationJob)
                .filter(
                    VegetationJob.tenant_id == tenant_id,
                    VegetationJob.entity_id == job.entity_id,
                    VegetationJob.job_type == "calculate_index",
                    VegetationJob.id != job.id,
                    or_(
                        VegetationJob.result["scene_id"].astext == scene_uuid,
                        VegetationJob.parameters["scene_id"].astext == scene_uuid,
                    ),
                )
                .all()
            )
            for c in children:
                deleted += _hard_delete_one(db, tenant_id, c)

    # 3. Drop the raster file from MinIO if the job produced one.
    raster_path = (job.result or {}).get("raster_path")
    if raster_path:
        _delete_raster_safely(raster_path, tenant_id)

    # 4. Drop the matching cache row(s) for calc_index jobs (kept for
    # backwards compatibility; the cache table is no longer written by the
    # current worker, but legacy rows may still exist).
    if job.job_type == "calculate_index":
        result = job.result or {}
        scene_uuid = result.get("scene_id")
        idx_type = result.get("index_type")
        if scene_uuid and idx_type:
            try:
                scene_uuid_obj = uuid_mod.UUID(scene_uuid)
                db.query(VegetationIndexCache).filter(
                    VegetationIndexCache.tenant_id == tenant_id,
                    VegetationIndexCache.entity_id == job.entity_id,
                    VegetationIndexCache.scene_id == scene_uuid_obj,
                    VegetationIndexCache.index_type == idx_type,
                ).delete(synchronize_session=False)
            except (ValueError, TypeError):
                pass
        # Also release the Redis idempotency lock so a fresh re-analysis
        # for the same (tenant, parcel, index, sensing_date) is allowed
        # immediately. Without this, deleting a calc_index job and
        # relaunching would surface 'skipped' for up to 24h.
        sensing = result.get("sensing_date") or (job.parameters or {}).get("sensing_date")
        if idx_type and sensing and job.entity_id:
            try:
                from app.tasks.processing_tasks import _release_idempotency
                _release_idempotency(tenant_id, job.entity_id, idx_type, sensing)
            except Exception as exc:
                logger.debug("Idempotency lock release failed (non-fatal): %s", exc)

    # 5. Delete the job row itself.
    db.delete(job)
    return deleted


class SeasonAnalyzeRequest(BaseModel):
    """Body for POST /parcels/{eid}/seasons/{sid}/analyze.

    Date range is *not* part of the request: it is derived from the season
    itself so a season-bound analysis cannot accidentally process scenes
    outside the campaign window.
    """
    indices: Optional[List[str]] = None
    custom_formulas: Optional[List[str]] = None
    local_cloud_threshold: Optional[float] = Field(None, ge=0, le=100)


@router.post("/{entity_id}/seasons/{season_id}/analyze")
async def analyze_in_season(
    entity_id: str,
    season_id: str,
    request: SeasonAnalyzeRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Launch an analysis bound to a specific crop season.

    Validates that the season belongs to the parcel + tenant and is not
    soft-deleted, then dispatches the same pipeline as /analyze but pins
    every resulting download job to crop_season_id and constrains the
    Copernicus search to the season's date range. End-date defaults to
    today when the season is open-ended.
    """
    tenant_id = current_user["tenant_id"]

    try:
        season_uuid = uuid_mod.UUID(season_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid season_id (expected UUID)")

    season = (
        db.query(VegetationCropSeason)
        .filter(
            VegetationCropSeason.id == season_uuid,
            VegetationCropSeason.tenant_id == tenant_id,
            VegetationCropSeason.entity_id == entity_id,
            VegetationCropSeason.deleted_at.is_(None),
        )
        .first()
    )
    if not season:
        raise HTTPException(
            status_code=404,
            detail="Crop season not found for this parcel.",
        )
    if not season.is_active:
        raise HTTPException(
            status_code=409,
            detail="Crop season is not active. Reactivate it before launching analyses.",
        )

    from datetime import date as _date_type
    start_date_iso = season.start_date.isoformat() if season.start_date else None
    end_date_iso = (
        season.end_date.isoformat() if season.end_date else _date_type.today().isoformat()
    )

    custom_formula_specs = _resolve_custom_formula_specs(
        db, tenant_id, request.custom_formulas or []
    )

    return await _dispatch_analyze_for_parcel(
        db=db,
        tenant_id=tenant_id,
        entity_id=entity_id,
        user_id=current_user.get("user_id"),
        indices=request.indices,
        custom_formula_specs=custom_formula_specs,
        start_date=start_date_iso,
        end_date=end_date_iso,
        local_cloud_threshold=request.local_cloud_threshold,
        crop_season_id=str(season.id),
    )


@router.delete("/{entity_id}/jobs/{job_id}", status_code=204)
async def delete_parcel_job(
    entity_id: str,
    job_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Hard-delete a job and everything it produced.

    Steps (each is best-effort + idempotent):
      1. Revoke Celery task if running.
      2. For 'download' jobs: cascade into matching calc_index children.
      3. Delete the raster file from MinIO (NoSuchKey → ignored).
      4. Delete vegetation_indices_cache rows tied to this job.
      5. Delete the row from vegetation_jobs.
      6. If the parcel has zero remaining completed calc_index jobs, also
         DELETE the per-parcel VegetationIndex entity from Orion-LD.

    Returns 204 even when the job did not exist (idempotent).
    """
    tenant_id = current_user["tenant_id"]
    try:
        job_uuid = uuid_mod.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job_id (expected UUID)")

    job = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.id == job_uuid,
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
        )
        .first()
    )
    if not job:
        # Idempotent: caller may be retrying after a transient error.
        return Response(status_code=204)

    deleted = _hard_delete_one(db, tenant_id, job)
    db.commit()

    # Best-effort entity cleanup (does not block the user's success path).
    # Wrapped so a transient httpx error after the DB delete still returns 204
    # — the orphan entity in Orion-LD will be reaped on the next delete.
    if job.entity_id:
        try:
            await _delete_orion_entity_if_orphan(job.entity_id, tenant_id, db)
        except Exception as exc:
            logger.warning(
                "Orion-LD orphan cleanup failed (non-fatal) for parcel %s: %s",
                job.entity_id, exc,
            )

    logger.info(
        "Hard-deleted %d vegetation_jobs row(s) starting from %s (tenant=%s, parcel=%s)",
        deleted, job_id, tenant_id, entity_id,
    )
    return Response(status_code=204)
