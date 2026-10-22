"""
MonitoringPeriod model — links an AgriParcel to a date range for monitoring.
"""

from sqlalchemy import Boolean, Column, Date, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import BaseModel, TenantMixin


class VegetationMonitoringPeriod(BaseModel, TenantMixin):
    __tablename__ = 'vegetation_monitoring_periods'

    entity_id = Column(Text, nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    label = Column(Text, nullable=True)
    monitoring_enabled = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


# Compatibility alias for code still referencing old name
VegetationCropSeason = VegetationMonitoringPeriod
