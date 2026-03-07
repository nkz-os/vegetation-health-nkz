# backend/app/api/scenes.py
"""
Scene query endpoints matching frontend API client expectations.
Routes: /api/vegetation/scenes, /api/vegetation/scenes/{entity_id}/stats,
        /api/vegetation/capabilities
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import Optional
import logging

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationScene, VegetationIndexCache

router = APIRouter(prefix="/api/vegetation", tags=["scenes"])
logger = logging.getLogger(__name__)


@router.get("/scenes")
async def list_scenes(
    entity_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """List vegetation scenes, optionally filtered by entity."""
    tenant_id = current_user["tenant_id"]

    query = (
        db.query(VegetationScene)
        .filter(VegetationScene.tenant_id == tenant_id, VegetationScene.is_valid == True)
    )

    if entity_id:
        scene_ids = (
            db.query(VegetationIndexCache.scene_id)
            .filter(
                VegetationIndexCache.tenant_id == tenant_id,
                VegetationIndexCache.entity_id == entity_id,
            )
            .distinct()
        )
        query = query.filter(VegetationScene.id.in_(scene_ids))

    if start_date:
        query = query.filter(VegetationScene.sensing_date >= start_date)
    if end_date:
        query = query.filter(VegetationScene.sensing_date <= end_date)

    total = query.count()
    scenes = query.order_by(desc(VegetationScene.sensing_date)).limit(limit).all()

    return {
        "scenes": [
            {
                "id": str(s.id),
                "scene_id": s.scene_id,
                "sensing_date": s.sensing_date.isoformat(),
                "acquisition_datetime": s.acquisition_datetime.isoformat() if s.acquisition_datetime else None,
                "cloud_coverage": s.cloud_coverage,
                "platform": s.platform,
                "is_valid": s.is_valid,
            }
            for s in scenes
        ],
        "total": total,
    }


@router.get("/scenes/{entity_id}/stats")
async def get_scene_stats(
    entity_id: str,
    index_type: str = Query("NDVI"),
    months: int = Query(12, le=36),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Aggregated stats for an entity's vegetation index over time."""
    tenant_id = current_user["tenant_id"]
    since = datetime.utcnow() - timedelta(days=months * 30)

    rows = (
        db.query(VegetationScene.sensing_date, VegetationIndexCache)
        .join(VegetationIndexCache, VegetationIndexCache.scene_id == VegetationScene.id)
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
            VegetationIndexCache.index_type == index_type.upper(),
            VegetationScene.sensing_date >= since.date(),
            VegetationScene.is_valid == True,
        )
        .order_by(VegetationScene.sensing_date.asc())
        .all()
    )

    data_points = []
    for sensing_date, cache in rows:
        data_points.append({
            "date": sensing_date.isoformat(),
            "mean": float(cache.mean_value) if cache.mean_value else None,
            "min": float(cache.min_value) if cache.min_value else None,
            "max": float(cache.max_value) if cache.max_value else None,
            "std_dev": float(cache.std_dev) if cache.std_dev else None,
        })

    # Overall stats
    agg = (
        db.query(
            func.avg(VegetationIndexCache.mean_value),
            func.min(VegetationIndexCache.min_value),
            func.max(VegetationIndexCache.max_value),
            func.count(VegetationIndexCache.id),
        )
        .join(VegetationScene, VegetationScene.id == VegetationIndexCache.scene_id)
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
            VegetationIndexCache.index_type == index_type.upper(),
            VegetationScene.sensing_date >= since.date(),
            VegetationScene.is_valid == True,
        )
        .first()
    )

    return {
        "entity_id": entity_id,
        "index_type": index_type.upper(),
        "months": months,
        "data_points": data_points,
        "summary": {
            "avg": float(agg[0]) if agg[0] else None,
            "min": float(agg[1]) if agg[1] else None,
            "max": float(agg[2]) if agg[2] else None,
            "count": agg[3] or 0,
        },
    }


@router.get("/capabilities")
async def get_capabilities(current_user: dict = Depends(require_auth)):
    """Return module capabilities for graceful degradation in frontend."""
    return {
        "n8n_available": False,
        "intelligence_available": False,
        "isobus_available": False,
        "features": {
            "predictions": False,
            "alerts_webhook": True,
            "export_isoxml": False,
            "send_to_cloud": False,
        },
    }
