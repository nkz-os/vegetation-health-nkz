"""
Service-to-service authentication middleware.
Validates X-Service-Auth header for Core Platform → Module communication.
"""

import os
import logging
from fastapi import HTTPException, status, Header
from typing import Optional

logger = logging.getLogger(__name__)

# Service authentication key (injected via environment)
MODULE_MANAGEMENT_KEY = os.getenv('MODULE_MANAGEMENT_KEY', '')

if not MODULE_MANAGEMENT_KEY:
    logger.warning(
        "MODULE_MANAGEMENT_KEY not set! Service authentication will fail. "
        "Set this environment variable in production."
    )


async def require_service_auth(
    x_service_auth: Optional[str] = Header(None, alias='X-Service-Auth')
) -> None:
    """FastAPI dependency to validate service-to-service authentication.
    
    This validates the X-Service-Auth header for endpoints that should only
    be accessible by the Core Platform (e.g., sync-limits).
    
    Args:
        x_service_auth: Value from X-Service-Auth header
        
    Raises:
        HTTPException 403 if authentication fails
    """
    if not MODULE_MANAGEMENT_KEY:
        logger.error("MODULE_MANAGEMENT_KEY not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Service authentication not configured"
        )
    
    if not x_service_auth:
        logger.warning("X-Service-Auth header missing")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Service-Auth header required"
        )
    
    # Constant-time comparison to prevent timing attacks
    if not _constant_time_compare(x_service_auth, MODULE_MANAGEMENT_KEY):
        logger.warning(f"Invalid X-Service-Auth key attempted (first 8 chars: {x_service_auth[:8]}...)")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service authentication key"
        )
    
    # Authentication successful
    logger.debug("Service authentication successful")


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks.
    
    Args:
        a: First string
        b: Second string
        
    Returns:
        True if strings are equal, False otherwise
    """
    if len(a) != len(b):
        return False
    
    result = 0
    for x, y in zip(a.encode('utf-8'), b.encode('utf-8')):
        result |= x ^ y
    
    return result == 0



