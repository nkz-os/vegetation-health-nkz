"""
Redis cache service for lazy tile caching.
"""

import logging
import os
import json
from typing import Optional
import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Cache TTL (24 hours in seconds)
TILE_CACHE_TTL = 24 * 60 * 60


class TileCache:
    """Redis-based tile cache for lazy caching strategy."""
    
    def __init__(self, redis_url: Optional[str] = None):
        """Initialize tile cache.
        
        Args:
            redis_url: Redis connection URL (defaults to REDIS_CACHE_URL or CELERY_BROKER_URL)
        """
        # Try REDIS_CACHE_URL first, fallback to CELERY_BROKER_URL
        redis_url = redis_url or os.getenv('REDIS_CACHE_URL') or os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
        
        try:
            # Use database 1 for cache (0 is for Celery) if not explicitly set
            if '/1' not in redis_url and '/0' in redis_url:
                redis_url = redis_url.replace('/0', '/1')
            elif '/1' not in redis_url and '/' not in redis_url.split('://')[1]:
                # No database specified, add /1
                redis_url = f"{redis_url}/1"
            
            self.redis_client = redis.from_url(redis_url, decode_responses=False)
            self.redis_client.ping()  # Test connection
            logger.info(f"Redis cache connected successfully: {redis_url}")
        except (RedisError, Exception) as e:
            logger.warning(f"Redis not available, cache disabled: {str(e)}")
            self.redis_client = None
    
    def _get_cache_key(self, z: int, x: int, y: int, scene_id: str, index_type: str) -> str:
        """Generate cache key for tile.
        
        Args:
            z: Zoom level
            x: Tile X coordinate
            y: Tile Y coordinate
            scene_id: Scene ID
            index_type: Index type
            
        Returns:
            Cache key string
        """
        return f"vegetation:tile:{scene_id}:{index_type}:{z}:{x}:{y}"
    
    def get_tile(self, z: int, x: int, y: int, scene_id: str, index_type: str) -> Optional[bytes]:
        """Get tile from cache.
        
        Args:
            z: Zoom level
            x: Tile X coordinate
            y: Tile Y coordinate
            scene_id: Scene ID
            index_type: Index type
            
        Returns:
            Tile PNG bytes if found, None otherwise
        """
        if not self.redis_client:
            return None
        
        try:
            cache_key = self._get_cache_key(z, x, y, scene_id, index_type)
            tile_data = self.redis_client.get(cache_key)
            
            if tile_data:
                logger.debug(f"Cache HIT for tile {z}/{x}/{y}")
                return tile_data
            
            logger.debug(f"Cache MISS for tile {z}/{x}/{y}")
            return None
            
        except RedisError as e:
            logger.error(f"Redis error getting tile: {str(e)}")
            return None
    
    def set_tile(
        self,
        z: int,
        x: int,
        y: int,
        scene_id: str,
        index_type: str,
        tile_data: bytes,
        ttl: int = TILE_CACHE_TTL
    ) -> bool:
        """Store tile in cache.
        
        Args:
            z: Zoom level
            x: Tile X coordinate
            y: Tile Y coordinate
            scene_id: Scene ID
            index_type: Index type
            tile_data: PNG tile bytes
            ttl: Time to live in seconds
            
        Returns:
            True if stored successfully
        """
        if not self.redis_client:
            return False
        
        try:
            cache_key = self._get_cache_key(z, x, y, scene_id, index_type)
            self.redis_client.setex(cache_key, ttl, tile_data)
            logger.debug(f"Cached tile {z}/{x}/{y} with TTL {ttl}s")
            return True
            
        except RedisError as e:
            logger.error(f"Redis error setting tile: {str(e)}")
            return False
    
    def invalidate_scene(self, scene_id: str) -> int:
        """Invalidate all tiles for a scene.
        
        Args:
            scene_id: Scene ID
            
        Returns:
            Number of keys deleted
        """
        if not self.redis_client:
            return 0
        
        try:
            pattern = f"vegetation:tile:{scene_id}:*"
            keys = self.redis_client.keys(pattern)
            
            if keys:
                deleted = self.redis_client.delete(*keys)
                logger.info(f"Invalidated {deleted} tiles for scene {scene_id}")
                return deleted
            
            return 0
            
        except RedisError as e:
            logger.error(f"Redis error invalidating scene: {str(e)}")
            return 0


# Global cache instance
_tile_cache: Optional[TileCache] = None


def get_tile_cache() -> TileCache:
    """Get or create global tile cache instance."""
    global _tile_cache
    if _tile_cache is None:
        _tile_cache = TileCache()
    return _tile_cache

