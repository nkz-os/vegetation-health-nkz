"""
Vegetation job model.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any
from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
    Integer, String, Text, JSON
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import BaseModel, TenantMixin


class VegetationJob(BaseModel, TenantMixin):
    """Asynchronous job for vegetation processing."""
    
    __tablename__ = 'vegetation_jobs'
    
    # Job metadata
    job_type = Column(String(50), nullable=False)
    status = Column(String(20), default='pending', nullable=False)
    priority = Column(Integer, default=5, nullable=False)
    
    # Job parameters
    parameters = Column(JSONB, default={}, nullable=False)
    
    # Entity context (FIWARE)
    entity_id = Column(Text, nullable=True, index=True)
    entity_type = Column(String(50), default='AgriParcel', nullable=False)
    
    # Geographic bounds
    bounds = Column(Geometry('POLYGON', srid=4326), nullable=True)
    
    # Date range
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    
    # Processing results
    result = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    error_traceback = Column(Text, nullable=True)
    
    # Celery task tracking
    celery_task_id = Column(Text, nullable=True, index=True)
    
    # Progress tracking
    progress_percentage = Column(Integer, default=0, nullable=False)
    progress_message = Column(Text, nullable=True)
    
    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    created_by = Column(Text, nullable=True)

    # Group jobs under a crop season (mandatory for new analyses, NULL for legacy rows).
    crop_season_id = Column(UUID(as_uuid=True), ForeignKey('vegetation_crop_seasons.id', ondelete='SET NULL'), nullable=True, index=True)

    # Soft-delete bookkeeping (the canonical hard-delete cascade lives in the API).
    deleted_at = Column(DateTime(timezone=True), nullable=True)


    __table_args__ = (
        CheckConstraint(
            "job_type IN ('download', 'process', 'calculate_index')",
            name='vegetation_jobs_job_type_check'
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name='vegetation_jobs_status_check'
        ),
        CheckConstraint(
            'priority >= 1 AND priority <= 10',
            name='vegetation_jobs_priority_check'
        ),
        CheckConstraint(
            'progress_percentage >= 0 AND progress_percentage <= 100',
            name='vegetation_jobs_progress_check'
        ),
    )
    
    def update_progress(self, percentage: int, message: Optional[str] = None) -> None:
        """Update job progress."""
        self.progress_percentage = max(0, min(100, percentage))
        if message:
            self.progress_message = message
    
    def mark_started(self) -> None:
        """Mark job as started."""
        self.status = 'running'
        self.started_at = datetime.utcnow()
    
    def mark_completed(self, result: Optional[Dict[str, Any]] = None) -> None:
        """Mark job as completed."""
        self.status = 'completed'
        self.completed_at = datetime.utcnow()
        self.progress_percentage = 100
        if result:
            self.result = result
    
    def mark_failed(self, error_message: str, error_traceback: Optional[str] = None) -> None:
        """Mark job as failed."""
        self.status = 'failed'
        self.completed_at = datetime.utcnow()
        self.error_message = error_message
        if error_traceback:
            self.error_traceback = error_traceback

