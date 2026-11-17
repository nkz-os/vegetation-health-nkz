"""
Authentication middleware — delegates to api-gateway headers.
Does NOT validate JWTs locally (platform convention).

The gateway signs the identity it forwards (X-Auth-Signature, canonical
platform HMAC over the bearer token and the tenant). With
REQUIRE_HMAC_SIGNATURE=true the signature is checked, so another pod cannot
pass itself off as a tenant; internal callers sign over an empty token.
"""
import logging
import os

from fastapi import Request, HTTPException, status
from nkz_platform_sdk.crypto import verify_hmac_signature

logger = logging.getLogger(__name__)

HMAC_SECRET = os.getenv("HMAC_SECRET", "")
REQUIRE_HMAC = os.getenv("REQUIRE_HMAC_SIGNATURE", "").lower() in ("1", "true", "yes")


def check_signature_config() -> None:
    """Called at startup: enforcing signatures without the secret would let nothing in."""
    if REQUIRE_HMAC and not HMAC_SECRET:
        raise RuntimeError("REQUIRE_HMAC_SIGNATURE is set but HMAC_SECRET is empty")
    if not REQUIRE_HMAC:
        logger.warning("REQUIRE_HMAC_SIGNATURE is off: tenant headers are trusted without a signature")


def _verify_signature(request: Request, tenant_id: str) -> None:
    if not REQUIRE_HMAC:
        return
    signature = request.headers.get("X-Auth-Signature", "")
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not verify_hmac_signature(HMAC_SECRET, signature, token, tenant_id, fail_open=False):
        logger.warning("rejected request without a valid gateway signature tenant=%s path=%s",
                       tenant_id, request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing signature")


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
    
    _verify_signature(request, tenant_id)
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
    _verify_signature(request, tenant_id)
    return tenant_id
