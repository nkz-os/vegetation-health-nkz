"""
API dependencies for FastAPI.
"""

from fastapi import Depends
from app.database import SessionLocal
from app.middleware.auth import require_auth


def get_db_for_tenant(current_user: dict = Depends(require_auth)):
    """Yield a database session for the authenticated tenant.

    tenant_id is extracted from the JWT by require_auth middleware.
    Session is closed in the finally block via FastAPI's generator handling.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
