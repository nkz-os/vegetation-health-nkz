"""Tests for platform Copernicus credential resolution — env-only, no DB.

`external_api_credentials.password_encrypted` is a one-way SHA-256 hash
(services/common/hash_utils.py:salted_credential_digest), never usable as
an OAuth client secret. The only working platform credential path is the
COPERNICUS_CLIENT_ID / COPERNICUS_CLIENT_SECRET env vars sourced from the
K8s secret `copernicus-cdse-secret`. These tests pin that contract and
guard against the DB read ever being reintroduced.
"""

import psycopg2
import pytest

from app.services.platform_credentials import get_copernicus_credentials


@pytest.fixture(autouse=True)
def _clear_copernicus_env(monkeypatch):
    monkeypatch.delenv("COPERNICUS_CLIENT_ID", raising=False)
    monkeypatch.delenv("COPERNICUS_CLIENT_SECRET", raising=False)


class TestGetCopernicusCredentialsEnvOnly:
    def test_returns_env_pair_when_both_set(self, monkeypatch):
        monkeypatch.setenv("COPERNICUS_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("COPERNICUS_CLIENT_SECRET", "test-client-secret")

        creds = get_copernicus_credentials()

        assert creds == {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "service_url": "https://dataspace.copernicus.eu",
            "auth_type": "basic_auth",
        }

    def test_returns_none_when_client_id_missing(self, monkeypatch):
        monkeypatch.delenv("COPERNICUS_CLIENT_ID", raising=False)
        monkeypatch.setenv("COPERNICUS_CLIENT_SECRET", "test-client-secret")

        assert get_copernicus_credentials() is None

    def test_returns_none_when_client_secret_missing(self, monkeypatch):
        monkeypatch.setenv("COPERNICUS_CLIENT_ID", "test-client-id")
        monkeypatch.delenv("COPERNICUS_CLIENT_SECRET", raising=False)

        assert get_copernicus_credentials() is None

    def test_returns_none_when_neither_set(self):
        assert get_copernicus_credentials() is None

    def test_never_touches_db_when_creds_present(self, monkeypatch):
        monkeypatch.setenv("COPERNICUS_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("COPERNICUS_CLIENT_SECRET", "test-client-secret")

        def _boom(*args, **kwargs):
            raise AssertionError("psycopg2.connect must never be called")

        monkeypatch.setattr(psycopg2, "connect", _boom)

        creds = get_copernicus_credentials()

        assert creds is not None

    def test_never_touches_db_when_creds_absent(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("psycopg2.connect must never be called")

        monkeypatch.setattr(psycopg2, "connect", _boom)

        assert get_copernicus_credentials() is None
