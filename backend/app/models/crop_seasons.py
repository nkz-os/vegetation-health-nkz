"""
CropSeason model — links an AgriParcel to a crop + date range.
"""

from sqlalchemy import Boolean, Column, Date, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import BaseModel, TenantMixin


class VegetationCropSeason(BaseModel, TenantMixin):
    __tablename__ = 'vegetation_crop_seasons'

    entity_id = Column(Text, nullable=False, index=True)
    crop_type = Column(String(50), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    label = Column(Text, nullable=True)
    monitoring_enabled = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
