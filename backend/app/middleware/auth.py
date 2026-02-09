"""
Authentication middleware for FastAPI.
"""

import logging
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient
import os

logger = logging.getLogger(__name__)

# JWT configuration (align with Nekazari platform: auth.artotxiki.com)
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'RS256')
JWT_ISSUER = os.getenv(
    'JWT_ISSUER',
    'https://auth.artotxiki.com/auth/realms/nekazari'
)
JWKS_URL = os.getenv(
    'JWKS_URL',
    'https://auth.artotxiki.com/auth/realms/nekazari/protocol/openid-connect/certs'
)

# Cache for JWKS
_jwks_client: Optional[PyJWKClient] = None


def get_jwks_client() -> PyJWKClient:
    """Get or create JWKS client."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(JWKS_URL)
    return _jwks_client


async def verify_token(token: str) -> dict:
    """Verify JWT token and return payload.
    
    Args:
        token: JWT token string
        
    Returns:
        Decoded token payload
        
    Raises:
        HTTPException if token is invalid
    """
    try:
        # First, decode without verification to check issuer
        unverified = jwt.decode(token, options={"verify_signature": False})
        token_issuer = unverified.get('iss')
        logger.debug("Token issuer check: %s", token_issuer)
        
        # Strict issuer validation - fail closed for security (exact whitelist, no suffix matching)
        if token_issuer != JWT_ISSUER:
            logger.warning(f"Issuer mismatch: token has '{token_issuer}', expected '{JWT_ISSUER}'. Rejecting token.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token issuer"
            )

        # Get signing key from JWKS
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Decode and verify token with strict issuer validation
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"verify_exp": True, "verify_iss": True}
        )
        
        return payload
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error verifying token: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(e)}"
        )


def get_tenant_id(request: Request) -> str:
    """Extract tenant ID from request headers.
    
    Args:
        request: FastAPI request object
        
    Returns:
        Tenant ID
        
    Raises:
        HTTPException if tenant ID is missing
    """
    tenant_id = request.headers.get('X-Tenant-ID')
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID header is required"
        )
    return tenant_id


async def get_current_user(request: Request) -> dict:
    """Get current user from request.
    
    Args:
        request: FastAPI request object
        
    Returns:
        User information from token
        
    Raises:
        HTTPException if authentication fails
    """
    # Get token from Authorization header
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = auth_header.split(' ')[1]
    
    # Verify token
    payload = await verify_token(token)
    
    return {
        'user_id': payload.get('sub'),
        'email': payload.get('email'),
        'username': payload.get('preferred_username'),
        'roles': payload.get('realm_access', {}).get('roles', []),
        'tenant_id': get_tenant_id(request)
    }


# Dependency for FastAPI
async def require_auth(request: Request) -> dict:
    """FastAPI dependency for authentication."""
    return await get_current_user(request)

