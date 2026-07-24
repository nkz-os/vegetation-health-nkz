"""Tests for SentinelHubClient — token, statistical, and process API calls."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date
from app.services.sentinel_hub_client import SentinelHubClient


@pytest.fixture
def client():
    return SentinelHubClient(
        client_id="test-id",
        client_secret="test-secret",
    )


class TestTokenManagement:
    @pytest.mark.asyncio
    async def test_get_token_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "fake-token-abc",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            token = await client.get_token()
            assert token == "fake-token-abc"
            assert client._access_token == "fake-token-abc"
            assert client._token_expires_at is not None

    @pytest.mark.asyncio
    async def test_get_token_cached(self, client):
        """Token is reused while not expired."""
        client._access_token = "cached-token"
        from datetime import datetime, timedelta, timezone
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

        token = await client.get_token()
        assert token == "cached-token"

    @pytest.mark.asyncio
    async def test_get_token_refresh_on_expiry(self, client):
        """Token is refreshed when expired (less than 5 min remaining)."""
        from datetime import datetime, timedelta, timezone
        client._access_token = "old-token"
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-token",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            token = await client.get_token()
            assert token == "new-token"

    @pytest.mark.asyncio
    async def test_get_token_auth_failure(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid credentials"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with pytest.raises(Exception, match="Authentication failed"):
                await client.get_token()


class TestStatisticalAPI:
    @pytest.mark.asyncio
    async def test_statistical_success(self, client):
        client._access_token = "token"
        from datetime import datetime, timedelta, timezone
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        geo = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        evalscript = "// ndvi"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "interval": {"from": "2026-07-15T00:00:00Z", "to": "2026-07-20T00:00:00Z"},
                    "outputs": {
                        "ndvi": {"bands": {"B0": {"stats": {
                            "mean": 0.72, "stDev": 0.15, "min": 0.1, "max": 0.95,
                            "percentiles": {"10": 0.35, "90": 0.88},
                        }}}},
                    },
                }
            ]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await client.statistical(
                geometry=geo,
                evalscript=evalscript,
                date_range=(date(2026, 7, 15), date(2026, 7, 20)),
                bands=["B04", "B08"],
            )
            assert len(result["data"]) == 1
            stats = result["data"][0]["outputs"]["ndvi"]["bands"]["B0"]["stats"]
            assert stats["mean"] == 0.72

    @pytest.mark.asyncio
    async def test_statistical_rate_limited(self, client):
        client._access_token = "token"
        from datetime import datetime, timedelta, timezone
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with pytest.raises(Exception, match="Rate limited"):
                await client.statistical(
                    geometry={"type": "Polygon", "coordinates": []},
                    evalscript="// ndvi",
                    date_range=(date(2026, 7, 1), date(2026, 7, 31)),
                    bands=["B04", "B08"],
                )


class TestProcessAPI:
    @pytest.mark.asyncio
    async def test_process_success(self, client):
        client._access_token = "token"
        from datetime import datetime, timedelta, timezone
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\x89PNGfakeimagedata"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            img = await client.process(
                bbox=[-5.0, 40.0, -4.9, 40.1],
                evalscript="// ndvi_color",
                width=256, height=256,
                date_str="2026-07-20",
            )
            assert img == b"\x89PNGfakeimagedata"
            assert len(img) > 0
