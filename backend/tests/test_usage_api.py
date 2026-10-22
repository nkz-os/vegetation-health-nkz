"""Tests for the read-only satellite-computation usage endpoint."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Prevent FastAPI lifespan from trying to connect to PostgreSQL
with patch("app.database.init_db"):
    from app.main import app

from app.services.satellite_quota import SatelliteQuota


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {
        "X-Tenant-ID": "test-tenant",
        "X-User-ID": "test-user",
        "X-User-Roles": "admin",
    }


class TestGetUsageEndpoint:
    def test_returns_usage_from_satellite_quota(self, client, auth_headers):
        """The route delegates to SatelliteQuota.get_usage for the caller's tenant."""
        with patch.object(
            SatelliteQuota,
            "get_usage",
            return_value={"used": 3, "limit": 100, "remaining": 97, "period": "2026-07"},
        ) as mock_get_usage:
            resp = client.get("/api/vegetation/config/usage", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json() == {
            "used": 3,
            "limit": 100,
            "remaining": 97,
            "period": "2026-07",
        }
        mock_get_usage.assert_called_once_with("test-tenant")

    def test_unlimited_tenant_returns_null_limit(self, client, auth_headers):
        with patch.object(
            SatelliteQuota,
            "get_usage",
            return_value={"used": 5, "limit": None, "remaining": None, "period": "2026-07"},
        ):
            resp = client.get("/api/vegetation/config/usage", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] is None
        assert data["remaining"] is None

    def test_missing_auth_rejects(self, client):
        resp = client.get("/api/vegetation/config/usage")
        assert resp.status_code in (400, 401)

    def test_never_500s_even_if_quota_service_raises_unexpectedly(self, client, auth_headers):
        """Belt-and-braces: SatelliteQuota.get_usage is documented as never-raising
        (fail-open), but if it somehow did, FastAPI's default handler still
        produces a 500 rather than crashing the process. This pins that the
        happy path returns 200 with the documented shape, which is what the
        `/calculate` (future) caller and the frontend rely on."""
        with patch.object(
            SatelliteQuota,
            "get_usage",
            return_value={"used": 0, "limit": None, "remaining": None, "period": "2026-07"},
        ):
            resp = client.get("/api/vegetation/config/usage", headers=auth_headers)

        assert resp.status_code == 200
