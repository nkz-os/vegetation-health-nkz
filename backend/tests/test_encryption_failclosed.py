"""Fernet encryption must fail closed in production.

VEGETATION_ENCRYPTION_KEY missing today silently degrades encrypt/decrypt
to a plaintext no-op — fine for local dev, a security hole in production.
These tests pin the required behavior:
  - ENV=production + no key -> RuntimeError (encrypt/decrypt, and startup
    verification)
  - non-production + no key -> dev no-op retained (plaintext passthrough)
  - decrypt_secret no longer swallows InvalidToken/decrypt errors
"""
import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _reset_fernet_singleton():
    """Each test reloads app.services.encryption fresh so the module-level
    _fernet cache and the ENV/VEGETATION_ENCRYPTION_KEY reads reflect the
    env vars set by that test, not leftovers from a previous test."""
    import app.services.encryption as enc
    importlib.reload(enc)
    yield
    # Leave the module in a clean (unloaded key) state for the next file/run.
    enc._fernet = None


def test_missing_key_raises_in_prod(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("VEGETATION_ENCRYPTION_KEY", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)
    with pytest.raises(RuntimeError, match="VEGETATION_ENCRYPTION_KEY"):
        enc.encrypt_secret("x")


def test_missing_key_allows_plaintext_in_dev(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("VEGETATION_ENCRYPTION_KEY", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)
    assert enc.encrypt_secret("x") == "x"  # dev no-op retained


def test_missing_key_allows_plaintext_when_env_unset(monkeypatch):
    """No ENV var at all (common local/CI case) must behave like dev, not prod."""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("VEGETATION_ENCRYPTION_KEY", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)
    assert enc.encrypt_secret("x") == "x"
    assert enc.decrypt_secret("x") == "x"


def test_decrypt_missing_key_raises_in_prod(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("VEGETATION_ENCRYPTION_KEY", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)
    with pytest.raises(RuntimeError, match="VEGETATION_ENCRYPTION_KEY"):
        enc.decrypt_secret("some-ciphertext")


def test_decrypt_reraises_invalid_token_instead_of_returning_ciphertext(monkeypatch):
    """A key mismatch (or corrupted ciphertext) must surface loudly, not
    silently return the garbage ciphertext as if it were plaintext."""
    from cryptography.fernet import Fernet, InvalidToken

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("VEGETATION_ENCRYPTION_KEY", key)
    monkeypatch.delenv("ENV", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)

    with pytest.raises(InvalidToken):
        enc.decrypt_secret("not-a-valid-fernet-token")


def test_encrypt_decrypt_roundtrip_when_key_set(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("VEGETATION_ENCRYPTION_KEY", key)
    monkeypatch.delenv("ENV", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)

    ciphertext = enc.encrypt_secret("my-secret-value")
    assert ciphertext != "my-secret-value"
    assert enc.decrypt_secret(ciphertext) == "my-secret-value"


def test_verify_encryption_ready_raises_in_prod_when_key_missing(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("VEGETATION_ENCRYPTION_KEY", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)
    with pytest.raises(RuntimeError, match="VEGETATION_ENCRYPTION_KEY"):
        enc.verify_encryption_ready()


def test_verify_encryption_ready_ok_in_dev_when_key_missing(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("VEGETATION_ENCRYPTION_KEY", raising=False)
    import app.services.encryption as enc
    importlib.reload(enc)
    enc.verify_encryption_ready()  # must not raise


def test_verify_encryption_ready_ok_in_prod_when_key_present(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("VEGETATION_ENCRYPTION_KEY", key)
    import app.services.encryption as enc
    importlib.reload(enc)
    enc.verify_encryption_ready()  # must not raise
