"""
Vegetation scene model.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any
from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, String, Text, BigInteger
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import BaseModel, TenantMixin


class VegetationScene(BaseModel, TenantMixin):
    """Sentinel-2 scene metadata."""
    
    __tablename__ = 'vegetation_scenes'
    
    # Sentinel-2 metadata
    scene_id = Column(Text, nullable=False)
    product_type = Column(String(20), default='S2MSI2A', nullable=False)
    platform = Column(String(20), default='Sentinel-2', nullable=False)
    
    # Temporal information
    sensing_date = Column(Date, nullable=False, index=True)
    acquisition_datetime = Column(DateTime(timezone=True), nullable=True)  # Exact STAC acquisition time (e.g. 10:51 UTC)
    ingestion_date = Column(DateTime(timezone=True), nullable=True)
    
    # Geographic information
    footprint = Column(Geometry('POLYGON', srid=4326), nullable=False)
    centroid = Column(Geometry('POINT', srid=4326), nullable=True)
    
    # Cloud information
    cloud_coverage = Column(Text, nullable=True)  # Stored as Decimal in DB, but Text in model for flexibility
    snow_coverage = Column(Text, nullable=True)
    
    # Storage information
    storage_path = Column(Text, nullable=False)
    storage_bucket = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    
    # Band information
    bands = Column(JSONB, nullable=True)  # {"B02": "path/to/B02.tif", ...}
    
    # Quality flags
    is_valid = Column(Boolean, default=True, nullable=False)
    quality_flags = Column(JSONB, default={}, nullable=False)
    
    # Job reference
    job_id = Column(UUID(as_uuid=True), ForeignKey('vegetation_jobs.id', ondelete='SET NULL'), nullable=True)
    
    __table_args__ = (
        {'comment': 'Sentinel-2 scene metadata with PostGIS geometry'},
    )
    
    def get_band_path(self, band: str) -> Optional[str]:
        """Get storage path for a specific band."""
        if not self.bands:
            return None
        return self.bands.get(band)
    
    def set_band_path(self, band: str, path: str) -> None:
        """Set storage path for a specific band."""
        if not self.bands:
            self.bands = {}
        self.bands[band] = path

