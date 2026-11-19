"""Modules that ask for continuous monitoring of a parcel (migration 018)."""
from uuid import uuid4

from sqlalchemy import Column, DateTime, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import Base


class VegetationContinuousRequest(Base):
    __tablename__ = "vegetation_continuous_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(Text, nullable=False)
    entity_id = Column(Text, nullable=False)
    module_id = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_id", "module_id", name="ux_vegetation_continuous_requests"),
    )
