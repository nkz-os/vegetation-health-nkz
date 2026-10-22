"""
Base models and mixins for Vegetation Prime module.
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class BaseModel(Base):
    """Base model with common fields."""
    
    __abstract__ = True
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
        }


class TenantMixin:
    """Mixin for tenant isolation."""
    
    tenant_id = Column(Text, nullable=False, index=True)
    
    def __init__(self, *args, tenant_id: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_id:
            self.tenant_id = tenant_id

