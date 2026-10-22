"""Tile token authentication — ephemeral HMAC tokens for Cesium tiles.

Uses a DEDICATED secret (TILE_TOKEN_SECRET), NOT the master HMAC_SECRET
used for inter-service auth. This limits blast radius if tile tokens leak.

FORMAT (aligned with platform canonical HMAC in keycloak_auth.py):
  Internal payload: {job_id}|{tenant_id}|{expiry}       (pipe-separated)
  Output:           {signature}:{expiry}                 (colon-separated, sig first)
  
  Generate: HMAC-SHA256(payload, secret) = full hex digest
  No truncation: full 64-char hex (same as platform standard).
"""
import hmac
import hashlib
import os
import time
from typing import Optional

_TILE_SECRET: Optional[str] = None


def _get_secret() -> str:
    global _TILE_SECRET
    if _TILE_SECRET is None:
        _TILE_SECRET = os.getenv("TILE_TOKEN_SECRET", "")
        if not _TILE_SECRET:
            # Fallback: generate random secret at startup (tokens lost on restart, OK)
            _TILE_SECRET = os.urandom(32).hex()
    return _TILE_SECRET


def generate_tile_token(job_id: str, tenant_id: str, ttl_seconds: int = 3600) -> str:
    """Generate a time-limited HMAC token for tile access.

    Platform-canonical format (see keycloak_auth.py:generate_hmac_signature):
      payload  = {job_id}|{tenant_id}|{expiry}
      output   = {HMAC-SHA256 hexdigest}:{expiry}

    Returns:
        Token string in format: {signature}:{expiry}
    """
    secret = _get_secret()
    expiry = int(time.time()) + ttl_seconds
    # Pipe separator matches platform standard (keycloak_auth.py:348)
    payload = f"{job_id}|{tenant_id}|{expiry}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{sig}:{expiry}"


def validate_tile_token(token: str, job_id: str, tenant_id: str) -> bool:
    """Validate a tile token against the expected job and tenant.

    Args:
        token: Token in format {signature}:{expiry}
        job_id: Job UUID to validate against
        tenant_id: Tenant ID to validate against

    Returns:
        True if token is valid and not expired, False otherwise.
    """
    try:
        parts = token.split(":")
        if len(parts) != 2:
            return False
        sig = parts[0]
        expiry = int(parts[1])
        if time.time() > expiry:
            return False
        secret = _get_secret()
        expected_payload = f"{job_id}|{tenant_id}|{expiry}"
        expected_sig = hmac.new(
            secret.encode(), expected_payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except (ValueError, IndexError):
        return False
