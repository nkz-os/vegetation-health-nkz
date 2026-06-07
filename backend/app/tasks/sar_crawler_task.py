"""
Celery task wrapper for the SAR crawler job.
"""
import logging
from app.celery_app import celery_app
from app.jobs.sar_crawler import sar_crawl_task

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="vegetation.sar_crawl",
    max_retries=2,
    default_retry_delay=3600,  # 1 hour between retries
    soft_time_limit=3600,      # 1 hour max
)
def sar_crawl(self):
    """Daily task: crawl Sentinel-1 GRD scenes and publish EOProduct entities."""
    try:
        result = sar_crawl_task()
        logger.info(
            "SAR crawl complete: parcels=%d scenes=%d created=%d errors=%d",
            result.get("parcels_processed", 0),
            result.get("scenes_found", 0),
            result.get("eo_products_created", 0),
            result.get("errors", 0),
        )
        if result.get("error"):
            logger.warning("SAR crawl returned error: %s", result["error"])
        return result
    except Exception as e:
        logger.error("SAR crawl failed: %s", e, exc_info=True)
        raise self.retry(exc=e)
