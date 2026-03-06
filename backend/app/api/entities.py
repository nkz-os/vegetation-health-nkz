# backend/app/api/entities.py
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from uuid import UUID, uuid4
from datetime import datetime
import os
import logging
from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationScene, VegetationIndexCache
from app.services.fiware_integration import FIWAREClient

router = APIRouter(prefix="/api/vegetation/entities", tags=["entities"])
logger = logging.getLogger(__name__)

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

@router.get("/{entity_id}/scenes/available")
async def get_available_scenes(
    entity_id: str, 
    index_type: str, 
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant)
):
    """Retorna metadatos para el timeline (fechas con datos válidos)."""
    tenant_id = current_user["tenant_id"]
    query = (
        db.query(VegetationScene, VegetationIndexCache)
        .join(VegetationIndexCache, VegetationIndexCache.scene_id == VegetationScene.id)
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
            VegetationIndexCache.index_type == index_type.upper(),
            VegetationScene.is_valid == True
        )
    )
    rows = query.order_by(VegetationScene.sensing_date.asc()).all()
    return {
        "timeline": [
            {
                "id": str(s.id),
                "date": s.sensing_date.isoformat(),
                "mean_value": float(c.mean_value) if c.mean_value else None,
                "cloud_pct": s.cloud_coverage
            } for s, c in rows
        ]
    }
