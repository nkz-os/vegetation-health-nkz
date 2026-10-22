"""
Vegetation configuration model.
"""

from typing import Optional
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Column, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import BaseModel, TenantMixin


class VegetationConfig(BaseModel, TenantMixin):
    """Configuration for vegetation processing per tenant."""
    
    __tablename__ = 'vegetation_config'
    
    # Copernicus credentials (encrypted)
    copernicus_client_id = Column(Text, nullable=True)
    copernicus_client_secret_encrypted = Column(Text, nullable=True)
    
    # Processing preferences
    default_index_type = Column(String(20), default='NDVI', nullable=False)
    cloud_coverage_threshold = Column(Numeric(5, 2), default=Decimal('50.0'), nullable=False)
    auto_process = Column(Boolean, default=True, nullable=False)
    
    # Storage configuration
    storage_type = Column(String(20), default='s3', nullable=False)
    storage_bucket = Column(Text, nullable=True)
    storage_path = Column(Text, default='vegetation-prime/', nullable=False)
    
    # Metadata
    created_by = Column(Text, nullable=True)
    
    __table_args__ = (
        UniqueConstraint('tenant_id', name='vegetation_config_tenant_unique'),
        CheckConstraint(
            "default_index_type IN ('NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE')",
            name='vegetation_config_index_type_check'
        ),
        CheckConstraint(
            'cloud_coverage_threshold >= 0 AND cloud_coverage_threshold <= 100',
            name='vegetation_config_cloud_threshold_check'
        ),
        CheckConstraint(
            "storage_type IN ('s3', 'minio', 'local')",
            name='vegetation_config_storage_type_check'
        ),
    )
    
    def to_dict(self) -> dict:
        """Convert to dictionary, excluding sensitive data."""
        data = super().to_dict()
        # Don't expose encrypted secret
        if 'copernicus_client_secret_encrypted' in data:
            data['copernicus_client_secret_encrypted'] = '***ENCRYPTED***'
        return data

