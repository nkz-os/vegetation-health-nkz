"""
Vegetation subscription models.
"""

from datetime import datetime
from sqlalchemy import Column, String, Boolean, Date, DateTime, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry

from app.models.base import BaseModel, TenantMixin

class VegetationSubscription(BaseModel, TenantMixin):
    """
    Subscription model for automated vegetation monitoring.
    Tracks which entities (parcels) should be updated automatically.
    """
    __tablename__ = "vegetation_subscriptions"
    
    # Target Entity
    entity_id = Column(String, nullable=False, index=True)  # AgriParcel ID
    entity_type = Column(String, default="AgriParcel", nullable=False)
    geometry = Column(Geometry('MULTIPOLYGON', srid=4326), nullable=False)
    
    # Configuration
    start_date = Column(Date, nullable=False)
    index_types = Column(ARRAY(String))  # e.g., ['NDVI', 'EVI']
    frequency = Column(String, default='weekly')  # daily, weekly, biweekly
    
    # Status
    is_active = Column(Boolean, default=True)
    status = Column(String, default="created")  # created, syncing, active, error
    last_run_at = Column(DateTime)
    next_run_at = Column(DateTime)
    last_error = Column(String, nullable=True)
    
    def __repr__(self):
        return f"<VegetationSubscription {self.id} (Entity: {self.entity_id})>"
