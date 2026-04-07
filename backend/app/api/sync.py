"""
Offline Vector Sync Endpoint for the Vegetation Health module (WatermelonDB format)
"""

import time
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import cast, String

from app.database import get_db_session
from app.models.indices import VegetationIndexCache
from app.middleware.auth import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation", tags=["sync"])

@router.get("/sync/vectorial", summary="WatermelonDB Sync for Vector Layers")
async def sync_vectorial(
    request: Request,
    last_pulled_at: int = Query(0, description="Timestamp of the last sync in milliseconds"),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db_session)
):
    """
    Returns vectorized GeoJSON polygons (from statistics_geojson) 
    compatible with WatermelonDB's Push/Pull spec for offline Mobile HMI sync.
    """
    try:
        current_ts = int(time.time() * 1000)
        
        # Query for newly processed vegetation caches that have vector data
        query = db.query(VegetationIndexCache).filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.statistics_geojson.isnot(None)
        )
        
        if last_pulled_at > 0:
            last_dt = datetime.fromtimestamp(last_pulled_at / 1000.0, tz=timezone.utc)
            # calculated_at is stored as String/Text in db according to models.py
            # So we compare strings if formatted correctly, or parse, or we use calculation_time
            # For robustness, we will filter in memory or rely on standard string comparison if ISO format
            query = query.filter(VegetationIndexCache.calculated_at >= last_dt.isoformat())
            
        caches = query.all()
        
        updated_layers = []
        created_layers = []
        
        for cache in caches:
            # We map the VegetationIndexCache to a frontend model: vegetation_vector_layers
            item = {
                "id": str(cache.id),
                "remote_id": str(cache.id),
                "entity_id": cache.entity_id,
                "scene_id": str(cache.scene_id),
                "index_type": cache.index_type,
                "geojson": cache.statistics_geojson,
                "created_at": current_ts,
                "updated_at": current_ts
            }
            
            # Map calculated_at to created_at
            if cache.calculated_at:
                try:
                    dt = datetime.fromisoformat(cache.calculated_at.replace("Z", "+00:00"))
                    item["created_at"] = int(dt.timestamp() * 1000)
                    item["updated_at"] = item["created_at"]
                except:
                    pass

            if last_pulled_at == 0:
                created_layers.append(item)
            else:
                updated_layers.append(item)
                
        return {
            "changes": {
                "vegetation_vector_layers": {
                    "created": created_layers,
                    "updated": updated_layers,
                    "deleted": []
                }
            },
            "timestamp": current_ts
        }
        
    except Exception as e:
        logger.error(f"Error in vectorial sync: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
