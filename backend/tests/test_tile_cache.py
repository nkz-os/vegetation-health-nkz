"""Tests for tile cache service and Sentinel Hub tile proxy endpoint."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
from io import BytesIO

from app.services import tile_cache


class TestTileCache:
    def test_cache_key_with_date(self):
        key = tile_cache.cache_key("t1", "NDVI", 12, 2000, 1500, "2026-07-20")
        assert key == "tiles/t1/ndvi/12/2000/1500/2026-07-20.png"

    def test_cache_key_without_date(self):
        key = tile_cache.cache_key("t2", "EVI", 10, 500, 300, None)
        assert key == "tiles/t2/evi/10/500/300/latest.png"

    def test_cache_key_lowercases_index(self):
        key = tile_cache.cache_key("t3", "NdVi", 8, 100, 100, None)
        assert key == "tiles/t3/ndvi/8/100/100/latest.png"

    def test_cache_key_follows_design(self):
        key = tile_cache.cache_key("tenant-abc", "SAVI", 14, 8000, 6000, "2026-06-15")
        assert key.startswith("tiles/tenant-abc/")

    def test_get_cached_tile_hit(self):
        tile_cache._s3 = None
        fake_data = b"\x89PNGtiledata"

        mock_s3 = MagicMock()
        mock_resp = {
            "Body": BytesIO(fake_data),
            "LastModified": datetime.now(timezone.utc),
        }
        mock_s3.get_object.return_value = mock_resp
        tile_cache._s3 = mock_s3

        result = tile_cache.get_cached_tile("t1", "NDVI", 12, 2000, 1500)
        assert result == fake_data
        mock_s3.get_object.assert_called_once()

    def test_get_cached_tile_expired(self):
        tile_cache._s3 = None
        fake_data = b"\x89PNGexpired"

        mock_s3 = MagicMock()
        mock_resp = {
            "Body": BytesIO(fake_data),
            "LastModified": datetime.now(timezone.utc) - timedelta(days=60),
        }
        mock_s3.get_object.return_value = mock_resp
        tile_cache._s3 = mock_s3

        result = tile_cache.get_cached_tile("t1", "NDVI", 12, 2000, 1500)
        assert result is None

    def test_get_cached_tile_miss(self):
        tile_cache._s3 = None
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        tile_cache._s3 = mock_s3

        result = tile_cache.get_cached_tile("t1", "NDVI", 12, 2000, 1500)
        assert result is None

    def test_put_cached_tile(self):
        tile_cache._s3 = None
        mock_s3 = MagicMock()
        tile_cache._s3 = mock_s3

        tile_cache.put_cached_tile("t1", "NDVI", 12, 2000, 1500, b"\x89PNGdata")
        mock_s3.put_object.assert_called_once()
