"""Fernet encryption helpers for sensitive config values.

Uses the VEGETATION_ENCRYPTION_KEY env var (Fernet key, 32 url-safe
base64-encoded bytes).

Fails closed in production: if ENV=production and the key is missing,
encrypt_secret/decrypt_secret/verify_encryption_ready raise RuntimeError
instead of silently degrading to plaintext. Outside production (local
dev, tests), a missing key is a deliberate no-op — secrets pass through
unchanged so the module runs without extra setup.
"""

import logging
import os

logger = logging.getLogger(__name__)

_fernet = None


def _is_production() -> bool:
    return os.getenv("ENV") == "production"


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.getenv("VEGETATION_ENCRYPTION_KEY", "")
    if not key:
        if _is_production():
            raise RuntimeError(
                "VEGETATION_ENCRYPTION_KEY is required in production"
            )
        logger.warning(
            "VEGETATION_ENCRYPTION_KEY not set — secrets will be stored "
            "as plaintext. Set this in production via K8s Secret."
        )
        _fernet = False  # sentinel: no encryption available (dev-only no-op)
        return False

    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return _fernet
    except ImportError:
        if _is_production():
            raise RuntimeError(
                "cryptography package is required for VEGETATION_ENCRYPTION_KEY in production"
            )
        logger.warning("cryptography not installed — secrets stored as plaintext")
        _fernet = False
        return False


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret value.

    Returns plaintext unchanged only outside production when no key is
    configured (dev no-op). Raises RuntimeError in production if the key
    is missing.
    """
    f = _get_fernet()
    if f is False:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a secret value.

    Returns ciphertext unchanged only outside production when no key is
    configured (dev no-op). Raises RuntimeError in production if the key
    is missing, and re-raises decrypt failures (e.g. InvalidToken) instead
    of silently returning the ciphertext — a key mismatch or corrupted
    value must surface loudly, not decrypt to garbage.
    """
    f = _get_fernet()
    if f is False:
        return ciphertext
    return f.decrypt(ciphertext.encode()).decode()


def verify_encryption_ready() -> None:
    """Verify encryption is ready for the current environment.

    Intended for startup checks (mirrors database.py's DATABASE_URL
    fail-fast pattern). Raises RuntimeError in production when
    VEGETATION_ENCRYPTION_KEY is missing; outside production it only
    logs a warning, preserving the dev no-op.
    """
    _get_fernet()
