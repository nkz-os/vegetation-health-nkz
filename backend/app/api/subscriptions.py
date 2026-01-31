"""
API endpoints for vegetation subscriptions (automated monitoring).
"""

from typing import List, Optional
from datetime import date
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from sqlalchemy import desc
from shapely.geometry import shape, mapping, MultiPolygon
from geoalchemy2.shape import to_shape

from app.models import VegetationSubscription
from app.middleware.auth import require_auth
from app.api.dependencies import get_db_for_tenant

router = APIRouter()

# --- Pydantic Models ---

class SubscriptionBase(BaseModel):
    entity_id: str = Field(..., description="Entity ID to monitor (e.g., AgriParcel ID)")
    geometry: dict = Field(..., description="GeoJSON geometry of the entity")
    start_date: date = Field(..., description="Monitoring start date")
    index_types: List[str] = Field(default=["NDVI"], description="List of indices to calculate")
    frequency: str = Field(default="weekly", description="Update frequency: weekly, daily")
    is_active: bool = Field(default=True, description="Whether subscription is active")

class SubscriptionCreate(SubscriptionBase):
    pass

class SubscriptionUpdate(BaseModel):
    index_types: Optional[List[str]] = None
    frequency: Optional[str] = None
    is_active: Optional[bool] = None
    status: Optional[str] = None

class SubscriptionResponse(SubscriptionBase):
    id: UUID
    tenant_id: str
    status: str
    last_run_at: Optional[date] = None
    next_run_at: Optional[date] = None
    last_error: Optional[str] = None
    created_at: date
    
    class Config:
        from_attributes = True

    @validator('geometry', pre=True)
    def parse_geometry(cls, v):
        # Handle GeoAlchemy2 WKBElement
        if hasattr(v, 'desc') or hasattr(v, 'geom_wkb'): 
             try:
                 s = to_shape(v)
                 return mapping(s)
             except Exception as e:
                 print(f"Error converting geometry: {e}")
                 return {}
        
        # Handle JSON String
        if isinstance(v, str):
            try:
                if v.strip().startswith('{'):
                    return json.loads(v)
                # If WKT string (though usually DB returns object), let it return as is? No, Pydantic expects dict
            except:
                pass
        
        # Handle Dict directly
        if isinstance(v, dict):
            return v
            
        return {}

# --- Endpoints ---

@router.post("/subscriptions", response_model=SubscriptionResponse)
async def create_subscription(
    subscription: SubscriptionCreate,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_for_tenant)
):
    """Create a new monitoring subscription."""
    # Check if subscription already exists for this entity
    existing = db.query(VegetationSubscription).filter(
        VegetationSubscription.tenant_id == current_user['tenant_id'],
        VegetationSubscription.entity_id == subscription.entity_id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Subscription already exists for this entity"
        )
    
    sub_data = subscription.dict()
    geom_dict = sub_data.pop('geometry')
    
    # Convert GeoJSON dict to WKT for GeoAlchemy
    try:
        s = shape(geom_dict)
        if s.geom_type == 'Polygon':
            s = MultiPolygon([s])
        
        # WKT with SRID for PostGIS
        sub_data['geometry'] = f"SRID=4326;{s.wkt}"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid geometry: {str(e)}")

    db_sub = VegetationSubscription(
        tenant_id=current_user['tenant_id'],
        status="created", # Initial status
        **sub_data
    )
    db.add(db_sub)
    db.commit()
    db.refresh(db_sub)
    return db_sub

@router.get("/subscriptions", response_model=List[SubscriptionResponse])
async def list_subscriptions(
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_for_tenant)
):
    """List all subscriptions."""
    subscriptions = db.query(VegetationSubscription).filter(
        VegetationSubscription.tenant_id == current_user['tenant_id']
    ).offset(skip).limit(limit).all()
    return subscriptions

@router.get("/subscriptions/{sub_id}", response_model=SubscriptionResponse)
async def get_subscription(
    sub_id: UUID,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_for_tenant)
):
    """Get subscription details."""
    sub = db.query(VegetationSubscription).filter(
        VegetationSubscription.id == sub_id,
        VegetationSubscription.tenant_id == current_user['tenant_id']
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub

@router.patch("/subscriptions/{sub_id}", response_model=SubscriptionResponse)
async def update_subscription(
    sub_id: UUID,
    updates: SubscriptionUpdate,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_for_tenant)
):
    """Update subscription."""
    sub = db.query(VegetationSubscription).filter(
        VegetationSubscription.id == sub_id,
        VegetationSubscription.tenant_id == current_user['tenant_id']
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
        
    for key, value in updates.dict(exclude_unset=True).items():
        setattr(sub, key, value)
        
    db.commit()
    db.refresh(sub)
    return sub

@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(
    sub_id: UUID,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_for_tenant)
):
    """Delete subscription."""
    sub = db.query(VegetationSubscription).filter(
        VegetationSubscription.id == sub_id,
        VegetationSubscription.tenant_id == current_user['tenant_id']
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
        
    db.delete(sub)
    db.commit()
    return {"message": "Subscription deleted"}
