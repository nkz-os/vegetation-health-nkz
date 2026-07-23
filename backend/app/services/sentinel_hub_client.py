"""Async Sentinel Hub API client — Statistical API + Process API.

Resolves credentials dynamically: tenant BYOK → platform fallback → env vars.
Uses httpx (already in requirements.txt) for async HTTP.
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Sentinel Hub OAuth2 endpoints
OAUTH_URL = "https://services.sentinel-hub.com/oauth/token"
STATISTICAL_URL = "https://services.sentinel-hub.com/api/v1/statistics"
PROCESS_URL = "https://services.sentinel-hub.com/api/v1/process"

# Timeouts (seconds)
DEFAULT_TIMEOUT = 30.0
STATISTICAL_TIMEOUT = 60.0
PROCESS_TIMEOUT = 15.0


class SentinelHubClient:
    """Async client for Sentinel Hub Statistical and Process APIs.

    Token lifecycle:
      - Token cached in memory, refreshed 5 min before expiry
      - On 401, token is invalidated and retried once
    """

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        instance_id: str | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._instance_id = instance_id
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._client: httpx.AsyncClient | None = None

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        """Update credentials and invalidate cached token."""
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = None
        self._token_expires_at = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        return self._client

    async def get_token(self) -> str:
        """Get a valid OAuth2 access token, refreshing if needed."""
        now = datetime.now(timezone.utc)
        if self._access_token and self._token_expires_at:
            if now < (self._token_expires_at - timedelta(minutes=5)):
                return self._access_token

        if not self._client_id or not self._client_secret:
            raise SentinelHubAuthError("Credentials not set")

        client = await self._get_client()
        try:
            resp = await client.post(
                OAUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                raise SentinelHubAuthError(
                    f"Authentication failed: HTTP {resp.status_code} — {resp.text[:200]}"
                )
            data = resp.json()
            self._access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self._token_expires_at = now + timedelta(seconds=expires_in)
            return self._access_token
        except httpx.HTTPError as e:
            raise SentinelHubAuthError(f"Authentication request failed: {e}") from e

    async def _auth_headers(self) -> dict[str, str]:
        token = await self.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def statistical(
        self,
        geometry: dict,
        evalscript: str,
        date_range: tuple[date, date],
        bands: list[str],
        cloud_cover_max: float = 50.0,
    ) -> dict[str, Any]:
        """Call Sentinel Hub Statistical API."""
        headers = await self._auth_headers()
        headers["Content-Type"] = "application/json"

        body = {
            "input": {
                "bounds": {
                    "geometry": geometry,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                },
                "data": [
                    {
                        "type": "sentinel-2-l2a",
                        "dataFilter": {
                            "timeRange": {
                                "from": f"{date_range[0].isoformat()}T00:00:00Z",
                                "to": f"{date_range[1].isoformat()}T23:59:59Z",
                            },
                            "maxCloudCoverage": int(cloud_cover_max),
                        },
                    }
                ],
            },
            "aggregation": {
                "timeRange": {
                    "from": f"{date_range[0].isoformat()}T00:00:00Z",
                    "to": f"{date_range[1].isoformat()}T23:59:59Z",
                },
                "aggregationInterval": {"of": "P5D"},
                "evalscript": evalscript,
            },
        }

        client = await self._get_client()
        try:
            resp = await client.post(
                STATISTICAL_URL,
                json=body,
                headers=headers,
                timeout=STATISTICAL_TIMEOUT,
            )
            if resp.status_code == 401:
                self._access_token = None
                raise SentinelHubAuthError("Token rejected by Statistical API")
            if resp.status_code == 429:
                raise SentinelHubRateLimitError(f"Rate limited: {resp.text[:200]}")
            if resp.status_code >= 500:
                raise SentinelHubServerError(f"Sentinel Hub server error {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException as e:
            raise SentinelHubTimeoutError(f"Statistical API timeout: {e}") from e

    async def process(
        self,
        bbox: list[float],
        evalscript: str,
        width: int = 256,
        height: int = 256,
        date_str: str | None = None,
    ) -> bytes:
        """Call Sentinel Hub Process API for tile rendering."""
        headers = await self._auth_headers()
        headers["Content-Type"] = "application/json"

        data_filter = {}
        if date_str:
            data_filter["timeRange"] = {
                "from": f"{date_str}T00:00:00Z",
                "to": f"{date_str}T23:59:59Z",
            }

        body: dict[str, Any] = {
            "input": {
                "bounds": {
                    "bbox": bbox,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                },
                "data": [{"type": "sentinel-2-l2a", "dataFilter": data_filter}],
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [
                    {"identifier": "default", "format": {"type": "image/png"}}
                ],
            },
            "evalscript": evalscript,
        }

        client = await self._get_client()
        try:
            resp = await client.post(
                PROCESS_URL,
                json=body,
                headers=headers,
                timeout=PROCESS_TIMEOUT,
            )
            if resp.status_code == 401:
                self._access_token = None
                raise SentinelHubAuthError("Token rejected by Process API")
            if resp.status_code == 429:
                raise SentinelHubRateLimitError(f"Rate limited: {resp.text[:200]}")
            if resp.status_code >= 500:
                raise SentinelHubServerError(f"Sentinel Hub server error {resp.status_code}")
            resp.raise_for_status()
            return resp.content
        except httpx.TimeoutException as e:
            raise SentinelHubTimeoutError(f"Process API timeout: {e}") from e

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# --- Exceptions ---

class SentinelHubError(Exception):
    """Base exception for Sentinel Hub client errors."""


class SentinelHubAuthError(SentinelHubError):
    """OAuth2 authentication failure (401/403)."""


class SentinelHubRateLimitError(SentinelHubError):
    """Rate limit exceeded (429)."""


class SentinelHubServerError(SentinelHubError):
    """Sentinel Hub 5xx server error."""


class SentinelHubTimeoutError(SentinelHubError):
    """Request timeout."""
