"""Zoning versions and technician notes (migration 016).

Only the current zoning is published to the context broker; pending proposals
and the history of versions live here.
"""
from uuid import uuid4

from sqlalchemy import Column, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base

VERSION_STATUSES = ("current", "pending", "superseded", "discarded")
VERSION_SOURCES = ("proposal", "merge")


class ZoningVersion(Base):
    __tablename__ = "zoning_versions"

    run_id = Column(Text, primary_key=True)
    tenant_id = Column(Text, nullable=False)
    parcel_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    derived_from = Column(Text, nullable=True)
    index_type = Column(Text, nullable=True)
    outcome_reason = Column(Text, nullable=True)
    entities = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list)
    job_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by = Column(Text, nullable=True)


class ZoningNote(Base):
    __tablename__ = "zoning_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(Text, nullable=False)
    parcel_id = Column(Text, nullable=False)
    zone_id = Column(Text, nullable=False)
    geometry = Column(JSONB, nullable=False)
    cause = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(Text, nullable=True)
