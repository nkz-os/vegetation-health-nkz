"""Tests for tenant config API — BYOK credential management."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Prevent FastAPI lifespan from trying to connect to PostgreSQL
with patch("app.database.init_db"):
    from app.main import app

from app.database import get_db_with_tenant, get_db_session


# Override the DB dependency for tests
def _fake_db():
    """Return a MagicMock that supports query chaining."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


@pytest.fixture
def client():
    app.dependency_overrides[get_db_with_tenant] = _fake_db
    app.dependency_overrides[get_db_session] = _fake_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    return {
        "X-Tenant-ID": "test-tenant",
        "X-User-ID": "test-user",
        "X-User-Roles": "admin",
    }


class TestUpsertConfig:
    def test_put_new_config_creates_record(self, client, auth_headers):
        """First PUT creates a new VegetationConfig row."""
        with patch("app.api.config.encrypt_secret", return_value="encrypted-secret-abc"):
            resp = client.put(
                "/api/vegetation/config",
                json={
                    "copernicus_client_id": "my-client-id",
                    "copernicus_client_secret": "my-secret",
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == "test-tenant"
        assert data["copernicus_client_id"] == "my-client-id"
        assert data["copernicus_configured"] is True

    def test_put_missing_client_id_rejects(self, client, auth_headers):
        resp = client.put(
            "/api/vegetation/config",
            json={"copernicus_client_secret": "my-secret"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_put_empty_client_id_rejects(self, client, auth_headers):
        resp = client.put(
            "/api/vegetation/config",
            json={"copernicus_client_id": "", "copernicus_client_secret": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_put_missing_auth_rejects(self, client):
        resp = client.put(
            "/api/vegetation/config",
            json={"copernicus_client_id": "x", "copernicus_client_secret": "y"},
        )
        assert resp.status_code in (400, 401)


class TestGetConfig:
    def test_get_no_config_returns_empty(self, client, auth_headers):
        resp = client.get("/api/vegetation/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == "test-tenant"
        assert data["copernicus_configured"] is False

    def test_get_config_does_not_leak_secret(self, client, auth_headers):
        """GET never returns the encrypted or raw secret."""
        resp = client.get("/api/vegetation/config", headers=auth_headers)
        data = resp.json()
        assert "copernicus_client_secret" not in data
        assert "secret" not in str(data).lower()


class TestDeleteConfig:
    def test_delete_clears_credentials(self, client, auth_headers):
        with patch("app.api.config.encrypt_secret", return_value="encrypted"):
            # First, set credentials
            client.put(
                "/api/vegetation/config",
                json={"copernicus_client_id": "x", "copernicus_client_secret": "y"},
                headers=auth_headers,
            )

        # Then delete
        resp = client.delete("/api/vegetation/config", headers=auth_headers)
        assert resp.status_code == 204


class TestEncryptionService:
    def test_encrypt_roundtrip_when_key_set(self):
        """encrypt → decrypt roundtrip works with a valid key."""
        from cryptography.fernet import Fernet
        import os
        from app.services import encryption

        # Force re-init with a test key
        encryption._fernet = None
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"VEGETATION_ENCRYPTION_KEY": key}):
            secret = encryption.encrypt_secret("my-secret-value")
            assert secret != "my-secret-value"
            assert encryption.decrypt_secret(secret) == "my-secret-value"

    def test_encrypt_noop_when_key_unset(self):
        """Without VEGETATION_ENCRYPTION_KEY, encrypt is a no-op."""
        import os
        from app.services import encryption

        encryption._fernet = None
        with patch.dict(os.environ, {}, clear=True):
            secret = encryption.encrypt_secret("plaintext")
            assert secret == "plaintext"
            assert encryption.decrypt_secret(secret) == "plaintext"
