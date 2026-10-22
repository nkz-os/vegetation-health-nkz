"""
Models for usage tracking and plan limits.
"""

from datetime import datetime
from typing import Optional
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Column, Integer, Numeric, String, Text, UniqueConstraint, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry

from .base import BaseModel, TenantMixin


class VegetationPlanLimits(BaseModel, TenantMixin):
    """Plan limits synchronized from Core Platform."""
    
    __tablename__ = 'vegetation_plan_limits'
    
    # Plan information
    plan_type = Column(String(50), default='basic', nullable=False)
    plan_name = Column(Text, nullable=True)
    
    # Volume limits (Hectares)
    monthly_ha_limit = Column(Numeric(12, 2), default=Decimal('10.0'), nullable=False)
    daily_ha_limit = Column(Numeric(12, 2), default=Decimal('5.0'), nullable=False)
    
    # Frequency limits (Jobs per day/month)
    daily_jobs_limit = Column(Integer, default=5, nullable=False)
    monthly_jobs_limit = Column(Integer, default=100, nullable=False)
    
    # Rate limiting per job type
    daily_download_jobs_limit = Column(Integer, default=3, nullable=False)
    daily_process_jobs_limit = Column(Integer, default=10, nullable=False)
    daily_calculate_jobs_limit = Column(Integer, default=20, nullable=False)
    
    # Metadata
    is_active = Column(Boolean, default=True, nullable=False)
    synced_at = Column(DateTime(timezone=True), nullable=False, server_default='now()')
    synced_by = Column(Text, nullable=True)
    
    __table_args__ = (
        UniqueConstraint('tenant_id', name='vegetation_plan_limits_tenant_unique'),
    )


class VegetationUsageStats(BaseModel, TenantMixin):
    """Monthly aggregated usage statistics."""
    
    __tablename__ = 'vegetation_usage_stats'
    
    # Time period
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    
    # Volume usage (Hectares)
    ha_processed = Column(Numeric(12, 4), default=Decimal('0.0'), nullable=False)
    ha_processed_count = Column(Integer, default=0, nullable=False)
    
    # Frequency usage (Jobs)
    jobs_created = Column(Integer, default=0, nullable=False)
    jobs_completed = Column(Integer, default=0, nullable=False)
    jobs_failed = Column(Integer, default=0, nullable=False)
    
    # Breakdown by job type
    download_jobs = Column(Integer, default=0, nullable=False)
    process_jobs = Column(Integer, default=0, nullable=False)
    calculate_jobs = Column(Integer, default=0, nullable=False)
    
    # Metadata
    first_job_at = Column(DateTime(timezone=True), nullable=True)
    last_job_at = Column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (
        UniqueConstraint('tenant_id', 'year', 'month', name='vegetation_usage_stats_tenant_period_unique'),
        CheckConstraint('month >= 1 AND month <= 12', name='vegetation_usage_stats_month_check'),
    )


class VegetationUsageLog(BaseModel, TenantMixin):
    """Detailed log of each job's usage."""
    
    __tablename__ = 'vegetation_usage_log'
    
    # Job reference
    job_id = Column(UUID(as_uuid=True), ForeignKey('vegetation_jobs.id', ondelete='CASCADE'), nullable=False)
    
    # Usage metrics
    ha_processed = Column(Numeric(12, 4), default=Decimal('0.0'), nullable=False)
    job_type = Column(String(50), nullable=False)
    
    # Timestamp
    processed_at = Column(DateTime(timezone=True), nullable=False, server_default='now()')
    
    # Metadata
    bounds = Column(Geometry('POLYGON', srid=4326), nullable=True)
