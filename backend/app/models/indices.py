"""
Vegetation index models.
"""

from datetime import datetime
from typing import Optional
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import BaseModel, TenantMixin


class VegetationIndexCache(BaseModel, TenantMixin):
    """Cached vegetation index calculations."""
    
    __tablename__ = 'vegetation_indices_cache'
    
    # Scene reference
    scene_id = Column(UUID(as_uuid=True), ForeignKey('vegetation_scenes.id', ondelete='CASCADE'), nullable=False)
    
    # Entity context
    entity_id = Column(Text, nullable=True, index=True)
    entity_type = Column(String(50), default='AgriParcel', nullable=False)
    
    # Index information
    index_type = Column(String(20), nullable=False)
    formula = Column(Text, nullable=True)  # For custom indices
    
    # Calculated values
    mean_value = Column(Numeric(10, 6), nullable=True)
    min_value = Column(Numeric(10, 6), nullable=True)
    max_value = Column(Numeric(10, 6), nullable=True)
    std_dev = Column(Numeric(10, 6), nullable=True)
    pixel_count = Column(Integer, nullable=True)
    
    # Spatial aggregation
    statistics_geojson = Column(JSONB, nullable=True)
    
    # Storage information
    result_raster_path = Column(Text, nullable=True)
    result_tiles_path = Column(Text, nullable=True)
    
    # Processing metadata
    calculated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    calculation_time_ms = Column(Integer, nullable=True)
    
    __table_args__ = (
        CheckConstraint(
            "index_type IN ('NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE', 'CUSTOM')",
            name='vegetation_indices_cache_index_type_check'
        ),
    )


class VegetationCustomFormula(BaseModel, TenantMixin):
    """User-defined custom index formulas."""
    
    __tablename__ = 'vegetation_custom_formulas'
    
    # Formula metadata
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    formula = Column(Text, nullable=False)  # Safe mathematical expression
    
    # Validation
    is_validated = Column(Boolean, nullable=False, default=False)
    validation_error = Column(Text, nullable=True)
    
    # Usage tracking
    usage_count = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    created_by = Column(Text, nullable=True)
    
    __table_args__ = (
        {'comment': 'User-defined custom vegetation index formulas'},
    )

# Force update
