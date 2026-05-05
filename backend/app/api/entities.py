# backend/app/api/entities.py
from fastapi import APIRouter, HTTPException, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from uuid import UUID, uuid4
from datetime import datetime, date as date_type
from typing import Optional
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

    # Available indices (DISTINCT index_type from cache)
    index_rows = (
        db.query(distinct(VegetationIndexCache.index_type))
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
        )
        .all()
    )
    available_indices = [r[0] for r in index_rows if r[0]]

    # Scene count (DISTINCT scene_id from cache for this entity)
    total_scenes = (
        db.query(func.count(distinct(VegetationIndexCache.scene_id)))
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
        )
        .scalar()
    ) or 0

    # Date range (min/max sensing_date via join)
    date_range = None
    latest_ndvi = None
    latest_sensing_date = None

    if total_scenes > 0:
        date_range_row = (
            db.query(
                func.min(VegetationScene.sensing_date),
                func.max(VegetationScene.sensing_date),
            )
            .join(VegetationIndexCache, VegetationIndexCache.scene_id == VegetationScene.id)
            .filter(
                VegetationIndexCache.tenant_id == tenant_id,
                VegetationIndexCache.entity_id == entity_id,
                VegetationScene.is_valid == True,
            )
            .first()
        )
        if date_range_row and date_range_row[0]:
            date_range = {
                "first": date_range_row[0].isoformat() if hasattr(date_range_row[0], 'isoformat') else str(date_range_row[0]),
                "last": date_range_row[1].isoformat() if hasattr(date_range_row[1], 'isoformat') else str(date_range_row[1]),
            }

        # Latest NDVI
        latest_row = (
            db.query(VegetationIndexCache.mean_value, VegetationScene.sensing_date)
            .join(VegetationScene, VegetationIndexCache.scene_id == VegetationScene.id)
            .filter(
                VegetationIndexCache.tenant_id == tenant_id,
                VegetationIndexCache.entity_id == entity_id,
                VegetationIndexCache.index_type == "NDVI",
                VegetationScene.is_valid == True,
            )
            .order_by(VegetationScene.sensing_date.desc())
            .first()
        )
        if latest_row:
            latest_ndvi = float(latest_row[0]) if latest_row[0] is not None else None
            latest_sensing_date = (
                latest_row[1].isoformat() if hasattr(latest_row[1], 'isoformat') else str(latest_row[1])
            )

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
    }


@router.get("/{entity_id}/scenes/available")
async def get_available_scenes(
    entity_id: str,
    index_type: Optional[str] = Query(None),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant)
):
    """Retorna metadatos para el timeline (fechas con datos válidos).

    Si no se especifica index_type, retorna escenas de todos los índices disponibles,
    deduplicadas por scene_id.
    """
    tenant_id = current_user["tenant_id"]

    query = (
        db.query(VegetationScene, VegetationIndexCache)
        .join(VegetationIndexCache, VegetationIndexCache.scene_id == VegetationScene.id)
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
            VegetationScene.is_valid == True,
        )
    )

    if index_type:
        query = query.filter(VegetationIndexCache.index_type == index_type.upper())

    rows = query.order_by(VegetationScene.sensing_date.asc()).all()

    # Deduplicate by scene_id when returning across all indices
    seen_scenes: set = set()
    timeline = []
    for s, c in rows:
        scene_key = str(s.id)
        if not index_type and scene_key in seen_scenes:
            continue
        seen_scenes.add(scene_key)
        timeline.append({
            "id": scene_key,
            "scene_id": scene_key,
            "date": s.sensing_date.isoformat(),
            "mean_value": float(c.mean_value) if c.mean_value else None,
            "local_cloud_pct": s.cloud_coverage,
            "cloud_pct": s.cloud_coverage,
            "raster_path": c.result_raster_path,
        })

    return {"timeline": timeline}
