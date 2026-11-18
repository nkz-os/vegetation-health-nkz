"""Per-parcel scene status (migration 017). Process state, not an observed series."""
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Date, DateTime, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import Base

PARCEL_SCENE_STATUSES = ("pending", "done", "cloud_skipped", "no_valid_pixels", "failed")


class VegetationParcelScene(Base):
    __tablename__ = "vegetation_parcel_scenes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(Text, nullable=False)
    entity_id = Column(Text, nullable=False)
    scene_id = Column(Text, nullable=False)  # Sentinel product id = VegetationScene.scene_id
    sensing_date = Column(Date, nullable=True)
    status = Column(Text, nullable=False)
    parcel_cloud_pct = Column(Numeric(5, 2), nullable=True)
    job_id = Column(UUID(as_uuid=True), nullable=True)
    attempts = Column(Integer, nullable=False, server_default=text("0"), default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_id", "scene_id", name="ux_vegetation_parcel_scenes"),
        CheckConstraint(
            "status IN ('pending', 'done', 'cloud_skipped', 'no_valid_pixels', 'failed')",
            name="vegetation_parcel_scenes_status_check",
        ),
    )
