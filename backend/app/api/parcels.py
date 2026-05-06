"""
Parcels API — overview + detail endpoints for the redesigned module page.

Replaces the scattered /entities/{id}/data-status + /results + /crop-seasons +
quota fetches with a single per-parcel overview that the new UI consumes in
one call. Also owns the hard-delete cascade for jobs.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import (
    VegetationCropSeason,
    VegetationIndexCache,
    VegetationJob,
    VegetationScene,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/parcels", tags=["parcels"])


# ── Serialisation helpers ───────────────────────────────────────────────

def _job_card(job: VegetationJob) -> Dict[str, Any]:
    """Compact job representation for the parcel detail UI."""
    result = job.result or {}
    skipped = bool(result.get("skipped_due_to_clouds"))
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
    latest = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
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

    # Available indices across all completed work for this parcel
    available_indices = [
        r[0]
        for r in (
            db.query(func.distinct(VegetationIndexCache.index_type))
            .filter(
                VegetationIndexCache.tenant_id == tenant_id,
                VegetationIndexCache.entity_id == entity_id,
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
