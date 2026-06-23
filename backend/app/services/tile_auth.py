"""Tile token authentication — ephemeral HMAC tokens for Cesium tiles.

Uses a DEDICATED secret (TILE_TOKEN_SECRET), NOT the master HMAC_SECRET
used for inter-service auth. This limits blast radius if tile tokens leak.
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
    """Generate a time-limited HMAC token for tile access."""
    secret = _get_secret()
    expiry = int(time.time()) + ttl_seconds
    payload = f"{job_id}:{tenant_id}:{expiry}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{expiry}:{sig}"


def validate_tile_token(token: str, job_id: str, tenant_id: str) -> bool:
    """Validate a tile token against the expected job and tenant."""
    try:
        parts = token.split(":")
        if len(parts) != 2:
            return False
        expiry = int(parts[0])
        sig = parts[1]
        if time.time() > expiry:
            return False
        secret = _get_secret()
        expected_payload = f"{job_id}:{tenant_id}:{expiry}"
        expected_sig = hmac.new(secret.encode(), expected_payload.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(sig, expected_sig)
    except (ValueError, IndexError):
        return False
