"""
Celery tasks for storage cleanup and retention.

- cleanup_global_cache: LRU eviction of Sentinel-2 bands from global bucket
  (based on last_accessed_at and download_count)
"""

import logging
from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app
from app.database import get_db_session
from app.models.global_scene_cache import GlobalSceneCache
from app.services.storage import create_storage_service, get_global_bucket_name

logger = logging.getLogger(__name__)

# Scenes accessed less than this many days ago are kept regardless of download_count
GLOBAL_CACHE_MAX_AGE_DAYS = int(__import__('os').getenv('GLOBAL_CACHE_MAX_AGE_DAYS', '30'))
# Scenes with download_count >= this threshold are kept even if old
GLOBAL_CACHE_MIN_DOWNLOADS = int(__import__('os').getenv('GLOBAL_CACHE_MIN_DOWNLOADS', '2'))
# Max scenes to process per run (avoid long locks)
GLOBAL_CACHE_BATCH_SIZE = 50


@celery_app.task(name='vegetation.cleanup_global_cache')
def cleanup_global_cache():
    """Evict old, low-reuse Sentinel-2 scenes from the global MinIO bucket.

    Selection criteria:
    - last_accessed_at older than GLOBAL_CACHE_MAX_AGE_DAYS (default 30)
    - download_count < GLOBAL_CACHE_MIN_DOWNLOADS (default 2)
    - is_valid = True (already-invalid entries are skipped)

    For each matching scene:
    1. Delete all band files from global bucket
    2. Mark is_valid = False in DB (metadata preserved for auditing)
    """
    db = next(get_db_session())
    cutoff = datetime.now(timezone.utc) - timedelta(days=GLOBAL_CACHE_MAX_AGE_DAYS)

    try:
        candidates = (
            db.query(GlobalSceneCache)
            .filter(
                GlobalSceneCache.is_valid == True,
                GlobalSceneCache.download_count < GLOBAL_CACHE_MIN_DOWNLOADS,
                GlobalSceneCache.last_accessed_at < cutoff,
            )
            .order_by(GlobalSceneCache.last_accessed_at.asc())
            .limit(GLOBAL_CACHE_BATCH_SIZE)
            .all()
        )

        if not candidates:
            logger.info("Global cache cleanup: no candidates for eviction")
            return

        logger.info(
            "Global cache cleanup: found %d candidate scenes (cutoff=%s, min_downloads=%d)",
            len(candidates), cutoff.isoformat(), GLOBAL_CACHE_MIN_DOWNLOADS,
        )

        global_bucket = get_global_bucket_name()
        storage = create_storage_service(
            storage_type=__import__('os').getenv('STORAGE_TYPE', 's3'),
            default_bucket=global_bucket,
        )

        deleted_scenes = 0
        deleted_files = 0

        for scene in candidates:
            try:
                # Delete band files from MinIO
                bands = scene.bands or {}
                for band_name, band_path in bands.items():
                    try:
                        storage.delete_file(band_path, global_bucket)
                        deleted_files += 1
                    except FileNotFoundError:
                        pass  # Already gone
                    except Exception as e:
                        logger.warning(
                            "Failed to delete band %s for scene %s: %s",
                            band_name, scene.scene_id, e,
                        )

                # Also try to clean the scene prefix directory
                if scene.storage_path:
                    try:
                        storage.delete_prefix(scene.storage_path, global_bucket)
                    except Exception:
                        pass  # Best-effort

                # Mark as invalid (keep metadata for auditing)
                scene.is_valid = False
                deleted_scenes += 1

            except Exception as e:
                logger.error(
                    "Error evicting scene %s: %s", scene.scene_id, e,
                )

        db.commit()
        logger.info(
            "Global cache cleanup complete: evicted %d scenes, deleted %d files",
            deleted_scenes, deleted_files,
        )

    except Exception as e:
        logger.error("Global cache cleanup failed: %s", e, exc_info=True)
        db.rollback()
    finally:
        db.close()
