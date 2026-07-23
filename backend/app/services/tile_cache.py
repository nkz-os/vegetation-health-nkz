"""Tile cache layer — MinIO-backed cache for Sentinel Hub Process API tiles.

Cache key: tiles/{index_type}/{z}/{x}/{y}/{date}.png
TTL: 30 days (configurable via TILE_CACHE_TTL_SECONDS)
"""

import logging
import os
from datetime import datetime, timezone
from io import BytesIO

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

TILE_CACHE_BUCKET = os.getenv("TILE_CACHE_BUCKET", "vegetation-tile-cache")
TILE_CACHE_TTL_SECONDS = int(os.getenv("TILE_CACHE_TTL_SECONDS", "2592000"))  # 30 days
S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "http://minio-service:9000")

_s3 = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            config=Config(s3={"addressing_style": "path"}),
        )
        _ensure_bucket()
    return _s3


def _ensure_bucket():
    try:
        _s3.head_bucket(Bucket=TILE_CACHE_BUCKET)
    except Exception:
        try:
            _s3.create_bucket(Bucket=TILE_CACHE_BUCKET)
            logger.info("Created tile cache bucket: %s", TILE_CACHE_BUCKET)
        except Exception as e:
            logger.warning("Could not create tile cache bucket: %s", e)


def cache_key(tenant_id: str, index_type: str, z: int, x: int, y: int, date_str: str | None = None) -> str:
    """Build a deterministic cache key for a tile, scoped to tenant."""
    idx = index_type.lower()
    if date_str:
        return f"tiles/{tenant_id}/{idx}/{z}/{x}/{y}/{date_str}.png"
    return f"tiles/{tenant_id}/{idx}/{z}/{x}/{y}/latest.png"


def get_cached_tile(tenant_id: str, index_type: str, z: int, x: int, y: int, date_str: str | None = None) -> bytes | None:
    """Retrieve a cached tile from MinIO. Returns None on cache miss."""
    key = cache_key(tenant_id, index_type, z, x, y, date_str)
    try:
        s3 = _get_s3()
        resp = s3.get_object(Bucket=TILE_CACHE_BUCKET, Key=key)

        # Check TTL via LastModified
        last_modified = resp["LastModified"]
        age = (datetime.now(timezone.utc) - last_modified).total_seconds()
        if age > TILE_CACHE_TTL_SECONDS:
            logger.debug("Tile cache expired for %s (age=%ds)", key, age)
            return None

        data = resp["Body"].read()
        logger.debug("Tile cache hit for %s (%d bytes)", key, len(data))
        return data
    except Exception:
        return None


def put_cached_tile(tenant_id: str, index_type: str, z: int, x: int, y: int, data: bytes, date_str: str | None = None) -> None:
    """Store a tile in the MinIO cache."""
    key = cache_key(tenant_id, index_type, z, x, y, date_str)
    try:
        s3 = _get_s3()
        s3.put_object(
            Bucket=TILE_CACHE_BUCKET,
            Key=key,
            Body=data,
            ContentType="image/png",
            CacheControl=f"public, max-age={TILE_CACHE_TTL_SECONDS}",
        )
        logger.debug("Tile cached: %s (%d bytes)", key, len(data))
    except Exception as e:
        logger.warning("Failed to cache tile %s: %s", key, e)
