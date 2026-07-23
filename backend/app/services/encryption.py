"""Fernet encryption helpers for sensitive config values.

Uses the VEGETATION_ENCRYPTION_KEY env var (Fernet key, 32 url-safe
base64-encoded bytes). If not set, encryption is a no-op and a warning
is logged — suitable for local dev, but production MUST set the key.
"""

import logging
import os

logger = logging.getLogger(__name__)

_fernet = None

def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.getenv("VEGETATION_ENCRYPTION_KEY", "")
    if not key:
        logger.warning(
            "VEGETATION_ENCRYPTION_KEY not set — secrets will be stored "
            "as plaintext. Set this in production via K8s Secret."
        )
        _fernet = False  # sentinel: no encryption available
        return False

    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return _fernet
    except ImportError:
        logger.warning("cryptography not installed — secrets stored as plaintext")
        _fernet = False
        return False


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret value. Returns plaintext if encryption is unavailable."""
    f = _get_fernet()
    if f is False:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a secret value. Returns ciphertext as-is if it's not encrypted."""
    f = _get_fernet()
    if f is False:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        # Ciphertext was stored before encryption was enabled — return as-is
        return ciphertext
