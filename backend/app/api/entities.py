# backend/app/api/entities.py
from fastapi import APIRouter, HTTPException, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from uuid import UUID, uuid4
import uuid as uuid_mod
from datetime import datetime, date as date_type
from typing import Dict, List, Optional
import os
import logging
import httpx
from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationScene, VegetationIndexCache, VegetationJob, VegetationCropSeason
from app.services.fiware_integration import FIWAREClient

router = APIRouter(prefix="/api/vegetation/entities", tags=["entities"])
logger = logging.getLogger(__name__)


async def _resolve_entity_name(entity_id: str, tenant_id: str) -> Optional[str]:
    """Query Orion-LD for the entity's name attribute."""
    try:
        orion_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        headers = {"Accept": "application/json", "NGSILD-Tenant": tenant_id}
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{orion_url}/ngsi-ld/v1/entities/{entity_id}",
                headers=headers,
                params={"attrs": "name"},
            )
            if resp.status_code == 200:
                data = resp.json()
                name_attr = data.get("name", {})
                if isinstance(name_attr, dict) and "value" in name_attr:
                    return name_attr["value"]
                if isinstance(name_attr, str):
                    return name_attr
    except Exception:
        pass
    return None

@router.post("/roi", status_code=status.HTTP_201_CREATED)
async def create_roi(request: dict, current_user: dict = Depends(require_auth)):
    """Crea una Management Zone (ROI) en Orion-LD."""
    try:
        cb_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        client = FIWAREClient(url=cb_url, tenant_id=current_user['tenant_id'])
        
        entity_id = f"urn:ngsi-ld:AgriParcel:{uuid4()}"
        entity = {
            "id": entity_id,
            "type": "AgriParcel",
            "name": {"type": "Property", "value": request.get("name")},
            "location": {"type": "GeoProperty", "value": request.get("geometry")},
            "category": {"type": "Property", "value": ["managementZone"]},
            "dateCreated": {"type": "Property", "value": datetime.now().isoformat()}
        }
        
        if request.get("parent_id"):
            entity["refParent"] = {"type": "Relationship", "object": request.get("parent_id")}
            
        client.create_entity(entity)
        return {"id": entity_id, "message": "ROI created successfully"}
    except Exception as e:
        logger.error(f"ROI creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{entity_id}/data-status")
async def get_entity_data_status(
    entity_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Discovery endpoint: returns all vegetation data available for an entity.

    Single call replaces N parallel fetches for crop seasons, subscription status,
    available indices, latest NDVI, scene count, and date range.
    """
    tenant_id = current_user["tenant_id"]

    # Available indices and scene count are derived from vegetation_jobs.result
    # rather than the (unwritten) vegetation_indices_cache table. The worker
    # only persists results to Orion-LD per the FIWARE mandate; no direct
    # writes to local SQL caches. Skipped calc_index rows (skipped=true,
    # raster_path=null) are filtered out so disabled pills only stay
    # disabled while there is genuinely no usable raster for that index.
    index_rows = (
        db.query(distinct(VegetationJob.result["index_type"].astext))
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
    available_indices = [r[0] for r in index_rows if r[0]]

    total_scenes = (
        db.query(func.count(distinct(VegetationJob.result["scene_id"].astext)))
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
            VegetationJob.result["raster_path"].astext.isnot(None),
        )
        .scalar()
    ) or 0

    # Date range, latest NDVI, latest sensing date — derived from
    # vegetation_jobs.result (not from the dead VegetationIndexCache).
    date_range = None
    latest_ndvi = None
    latest_sensing_date = None

    if total_scenes > 0:
        non_skipped_filter = [
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
            VegetationJob.result["raster_path"].astext.isnot(None),
        ]
        sensing_dates = (
            db.query(distinct(VegetationJob.result["sensing_date"].astext))
            .filter(*non_skipped_filter)
            .all()
        )
        sensing_strs = sorted(d[0] for d in sensing_dates if d[0])
        if sensing_strs:
            date_range = {"first": sensing_strs[0], "last": sensing_strs[-1]}

        latest_ndvi_job = (
            db.query(VegetationJob)
            .filter(
                *non_skipped_filter,
                VegetationJob.result["index_type"].astext == "NDVI",
            )
            .order_by(VegetationJob.result["sensing_date"].astext.desc())
            .first()
        )
        if latest_ndvi_job and latest_ndvi_job.result:
            stats = latest_ndvi_job.result.get("statistics") or {}
            mean = stats.get("mean")
            if mean is not None:
                try:
                    latest_ndvi = float(mean)
                except (TypeError, ValueError):
                    latest_ndvi = None
            latest_sensing_date = latest_ndvi_job.result.get("sensing_date")

    # Active crop seasons
    crop_seasons = (
        db.query(VegetationCropSeason)
        .filter(
            VegetationCropSeason.tenant_id == tenant_id,
            VegetationCropSeason.entity_id == entity_id,
            VegetationCropSeason.is_active == True,
        )
        .order_by(VegetationCropSeason.start_date.desc())
        .all()
    )
    active_crop_seasons = [
        {
            "id": str(s.id),
            "crop_type": s.crop_type,
            "start_date": s.start_date.isoformat() if s.start_date else None,
            "end_date": s.end_date.isoformat() if s.end_date else None,
            "monitoring_enabled": s.monitoring_enabled,
        }
        for s in crop_seasons
    ]

    # Active jobs for this entity
    active_jobs_count = (
        db.query(func.count(VegetationJob.id))
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.status.in_(["pending", "running"]),
        )
        .scalar()
    ) or 0

    # Recent download jobs that completed but were skipped because of clouds.
    # Surface this so the UI can tell the user why no layer is showing,
    # instead of looking like the analysis silently failed.
    recent_skips_q = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "download",
            VegetationJob.status == "completed",
        )
        .order_by(VegetationJob.completed_at.desc())
        .limit(10)
        .all()
    )
    recent_cloud_skips = [
        {
            "job_id": str(j.id),
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            "scene_id": (j.result or {}).get("scene_id"),
            "sensing_date": (j.result or {}).get("sensing_date"),
            "local_cloud_pct": (j.result or {}).get("local_cloud_pct"),
            "local_cloud_threshold": (j.result or {}).get("local_cloud_threshold"),
            "message": (j.result or {}).get("message"),
        }
        for j in recent_skips_q
        if (j.result or {}).get("skipped_due_to_clouds")
    ]

    # Resolve entity name from Orion-LD
    name = await _resolve_entity_name(entity_id, tenant_id)

    return {
        "entity_id": entity_id,
        "name": name,
        "has_any_data": total_scenes > 0 or active_jobs_count > 0,
        "available_indices": available_indices,
        "total_scenes": total_scenes,
        "date_range": date_range,
        "latest_ndvi": latest_ndvi,
        "latest_sensing_date": latest_sensing_date,
        "active_crop_seasons": active_crop_seasons,
        "active_jobs_count": active_jobs_count,
        "recent_cloud_skips": recent_cloud_skips,
    }


@router.get("/{entity_id}/scenes/available")
async def get_available_scenes(
    entity_id: str,
    index_type: Optional[str] = Query(None),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant)
):
    """Timeline metadata (dates with usable data) derived from
    vegetation_jobs.result. The legacy implementation joined the
    VegetationIndexCache table which is no longer written by the FIWARE-
    canonical worker — it always returned an empty timeline regardless
    of how many real rasters the parcel had.

    Each row exposes scene_id, date, mean value of the chosen index and
    the per-parcel local cloud percentage (from VegetationScene when
    we can resolve the UUID, null otherwise).
    """
    tenant_id = current_user["tenant_id"]

    base_filters = [
        VegetationJob.tenant_id == tenant_id,
        VegetationJob.entity_id == entity_id,
        VegetationJob.job_type == "calculate_index",
        VegetationJob.status == "completed",
        VegetationJob.deleted_at.is_(None),
        VegetationJob.result["raster_path"].astext.isnot(None),
    ]
    if index_type:
        base_filters.append(VegetationJob.result["index_type"].astext == index_type.upper())

    jobs = (
        db.query(VegetationJob)
        .filter(*base_filters)
        .order_by(VegetationJob.result["sensing_date"].astext.asc())
        .limit(2000)
        .all()
    )

    # Resolve cloud_coverage from VegetationScene in a single batch query
    # so the timeline can colour-code overcast scenes without N round trips.
    scene_uuid_strs = {
        (j.result or {}).get("scene_id") for j in jobs if (j.result or {}).get("scene_id")
    }
    scene_uuids: List[uuid_mod.UUID] = []
    for s in scene_uuid_strs:
        try:
            scene_uuids.append(uuid_mod.UUID(s))
        except (ValueError, TypeError):
            continue
    cloud_by_scene: Dict[str, Optional[str]] = {}
    if scene_uuids:
        for sid, cov in (
            db.query(VegetationScene.id, VegetationScene.cloud_coverage)
            .filter(
                VegetationScene.tenant_id == tenant_id,
                VegetationScene.id.in_(scene_uuids),
            )
            .all()
        ):
            cloud_by_scene[str(sid)] = cov

    # Deduplicate by scene_id when caller did not pin an index_type
    seen_scenes: set = set()
    timeline = []
    for job in jobs:
        result = job.result or {}
        scene_uuid = result.get("scene_id")
        if not scene_uuid:
            continue
        if not index_type and scene_uuid in seen_scenes:
            continue
        seen_scenes.add(scene_uuid)
        sensing = result.get("sensing_date")
        stats = result.get("statistics") or {}
        mean = stats.get("mean")
        try:
            mean_f = float(mean) if mean is not None else None
        except (ValueError, TypeError):
            mean_f = None
        cov = cloud_by_scene.get(scene_uuid)
        timeline.append({
            "id": scene_uuid,
            "scene_id": scene_uuid,
            "date": sensing,
            "mean_value": mean_f,
            "local_cloud_pct": cov,
            "cloud_pct": cov,
            "raster_path": result.get("raster_path"),
        })

    return {"timeline": timeline, "count": len(timeline)}
