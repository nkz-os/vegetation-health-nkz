"""
Global scene cache model for shared Sentinel-2 scene storage.
This allows multiple tenants to reuse the same downloaded scenes without
re-downloading from Copernicus, saving quota.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any
from decimal import Decimal

from sqlalchemy import Boolean, Column, Date, DateTime, String, Text, BigInteger, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import BaseModel


class GlobalSceneCache(BaseModel):
    """Global cache for Sentinel-2 scenes (shared across all tenants).
    
    This table stores metadata about scenes that have been downloaded
    from Copernicus and stored in the global bucket. When a tenant
    requests a scene, we first check this cache. If it exists, we
    copy it to the tenant's bucket instead of downloading again.
    """
    
    __tablename__ = 'global_scene_cache'
    
    # Sentinel-2 scene identifier (unique product ID from Copernicus)
    scene_id = Column(Text, nullable=False, unique=True, index=True)
    product_type = Column(String(20), default='S2MSI2A', nullable=False)
    platform = Column(String(20), default='Sentinel-2', nullable=False)
    
    # Temporal information
    sensing_date = Column(Date, nullable=False, index=True)
    ingestion_date = Column(DateTime(timezone=True), nullable=True)
    
    # Storage information (global bucket)
    storage_path = Column(Text, nullable=False)  # Path in global bucket
    storage_bucket = Column(Text, nullable=False)  # Global bucket name (e.g., 'vegetation-prime-global')
    file_size_bytes = Column(BigInteger, nullable=True)
    
    # Band information (paths to raw bands in global bucket)
    bands = Column(JSONB, nullable=True)  # {"B02": "path/to/B02.tif", "B04": "...", ...}
    
    # Metadata from Copernicus
    cloud_coverage = Column(Text, nullable=True)
    snow_coverage = Column(Text, nullable=True)
    footprint_geometry = Column(Text, nullable=True)  # GeoJSON string (can be converted to PostGIS if needed)
    
    # Cache metadata
    download_count = Column(Integer, default=0, nullable=False)  # How many times this scene has been reused
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)  # Last time a tenant requested this scene
    is_valid = Column(Boolean, default=True, nullable=False)  # Mark as invalid if files are corrupted/missing
    
    # Quality flags
    quality_flags = Column(JSONB, default={}, nullable=False)
    
    __table_args__ = (
        {'comment': 'Global cache for Sentinel-2 scenes shared across all tenants'},
    )
    
    def get_band_path(self, band: str) -> Optional[str]:
        """Get storage path for a specific band in global bucket."""
        if not self.bands:
            return None
        return self.bands.get(band)
    
    def set_band_path(self, band: str, path: str) -> None:
        """Set storage path for a specific band."""
        if not self.bands:
            self.bands = {}
        self.bands[band] = path
    
    def increment_download_count(self):
        """Increment the reuse counter and update last accessed time."""
        self.download_count = (self.download_count or 0) + 1
        self.last_accessed_at = datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with all fields."""
        return {
            'id': str(self.id),
            'scene_id': self.scene_id,
            'product_type': self.product_type,
            'platform': self.platform,
            'sensing_date': self.sensing_date.isoformat() if self.sensing_date else None,
            'ingestion_date': self.ingestion_date.isoformat() if self.ingestion_date else None,
            'storage_path': self.storage_path,
            'storage_bucket': self.storage_bucket,
            'file_size_bytes': self.file_size_bytes,
            'bands': self.bands,
            'cloud_coverage': self.cloud_coverage,
            'snow_coverage': self.snow_coverage,
            'download_count': self.download_count,
            'last_accessed_at': self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            'is_valid': self.is_valid,
            'quality_flags': self.quality_flags,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }















