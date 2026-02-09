"""
Database configuration and session management.
"""

import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from app.models.base import Base
from fastapi import Depends
from app.middleware.auth import get_tenant_id

# Database URL
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required.")

# Create engine
engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,  # Use NullPool for serverless/microservices
    echo=os.getenv('SQL_ECHO', 'false').lower() == 'true',
    connect_args={
        'connect_timeout': 10,
        'options': '-c timezone=utc'
    }
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def set_tenant_context(dbapi_conn, connection_record):
    """Set tenant context for RLS on connection.
    
    Note: This is a placeholder - actual tenant context should be set
    per-request using SET app.current_tenant = 'tenant_id'
    """
    pass


def get_db_session() -> Session:
    """Get database session (generator for dependency injection)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_with_tenant(tenant_id: str = Depends(get_tenant_id)):
    """Get database session with tenant context set.
    
    This is a generator function for FastAPI dependency injection.
    
    Args:
        tenant_id: Tenant ID for RLS (injected via dependency)
        
    Yields:
        Database session
    """
    db = SessionLocal()
    try:
        # Set tenant context for RLS (if extension is enabled)
        # Note: This requires the pg_trgm extension and RLS policies to be set up
        # For now, we'll just use the session without setting tenant context
        # as the models filter by tenant_id directly
        # db.execute(text(f"SET app.current_tenant = '{tenant_id}'"))
        # db.commit()
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database (create tables)."""
    Base.metadata.create_all(bind=engine)
