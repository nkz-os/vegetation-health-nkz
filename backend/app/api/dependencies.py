"""
API dependencies for FastAPI.
"""

from fastapi import Depends
from app.database import get_db_with_tenant
from app.middleware.auth import require_auth

# Helper function for database dependency with tenant context
def get_db_for_tenant(current_user: dict = Depends(require_auth)):
    """Get database session with tenant context.
    
    This is a FastAPI dependency that depends on current_user.
    Returns a generator that FastAPI will handle automatically.
    """
    # Call get_db_with_tenant and yield from it
    for db in get_db_with_tenant(current_user['tenant_id']):
        yield db
