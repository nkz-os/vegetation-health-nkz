"""
Authentication middleware — delegates to api-gateway headers.
Does NOT validate JWTs locally (platform convention).
"""
import logging
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)


async def require_auth(request: Request) -> dict:
    """FastAPI dependency: extract user from gateway-injected headers.
    
    The api-gateway validates the JWT and injects X-Tenant-ID, X-User-ID, 
    X-User-Roles. This module trusts those headers.
    """
    tenant_id = request.headers.get("X-Tenant-ID")
    user_id = request.headers.get("X-User-ID")
    roles_header = request.headers.get("X-User-Roles", "")
    
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID header is required",
        )
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-ID header is required",
        )
    
    roles = [r.strip() for r in roles_header.split(",") if r.strip()]
    
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "roles": roles,
    }


async def get_current_user(request: Request) -> dict:
    """Alias for require_auth — used as FastAPI dependency."""
    return await require_auth(request)


def get_tenant_id(request: Request) -> str:
    """Extract tenant ID from request header."""
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID header is required",
        )
    return tenant_id
