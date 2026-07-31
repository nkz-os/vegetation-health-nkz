"""Tests for SentinelHubClient.process_raster() — FLOAT32 GeoTIFF via Process API."""

import pytest
from unittest.mock import AsyncMock, patch
from app.services.sentinel_hub_client import SentinelHubClient

GEOM = {
    "type": "Polygon",
    "coordinates": [[[-2.08, 42.63], [-2.07, 42.63], [-2.07, 42.64], [-2.08, 42.64], [-2.08, 42.63]]],
}


@pytest.mark.asyncio
async def test_process_raster_posts_tiff_request_and_returns_bytes():
    c = SentinelHubClient("id", "secret")
    captured = {}

    class _Resp:
        status_code = 200
        content = b"II*\x00TIFFBYTES"  # fake GeoTIFF magic

        def raise_for_status(self):
            pass

    async def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _Resp()

    mock_client = type("X", (), {"post": staticmethod(fake_post)})

    with patch.object(c, "_auth_headers", AsyncMock(return_value={})), \
         patch.object(c, "_get_client", AsyncMock(return_value=mock_client)):
        out = await c.process_raster(GEOM, "//eval//", "2026-07-20", resx=0.0001, resy=0.0001)

    assert out == b"II*\x00TIFFBYTES"
    body = captured["body"]
    # TIFF output requested
    assert body["output"]["responses"][0]["format"]["type"] == "image/tiff"
    # geometry bounds + single-date time range
    assert body["input"]["bounds"]["geometry"] == GEOM
    df = body["input"]["data"][0]["dataFilter"]["timeRange"]
    assert df["from"].startswith("2026-07-20") and df["to"].startswith("2026-07-20")
    # dynamic resolution passed through
    assert body["output"]["resx"] == 0.0001 and body["output"]["resy"] == 0.0001
    assert body["evalscript"] == "//eval//"
