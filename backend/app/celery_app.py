"""
Celery application configuration for Vegetation Prime module.
"""

import logging
import os
import shutil

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready
from kombu import Queue

logger = logging.getLogger(__name__)

# Root for ephemeral per-scene band/Sen2Res working dirs (pod-local /tmp).
PROCESSING_ROOT = "/tmp/vegetation_processing"


def _sweep_processing_root(root: str = PROCESSING_ROOT) -> bool:
    """Remove the per-scene processing root. Returns True if something was removed.

    Called once at worker boot (worker_ready) — never per child recycle — so no
    concurrent calc_index task is using the shared bands. Catches orphans left by
    jobs that died before their last index; the per-job last-index cleanup in
    processing_tasks handles the normal case.
    """
    try:
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
            logger.info("Worker boot: swept stale processing dir %s", root)
            return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Worker boot sweep failed for %s: %s", root, exc)
    return False


@worker_ready.connect
def _on_worker_ready(**_kwargs):
    _sweep_processing_root()

# Create Celery app
celery_app = Celery(
    'vegetation_prime',
    broker=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
    include=['app.tasks']
)

# Dedicated queue: do NOT use the default 'celery' queue, since other Celery
# apps in the cluster (e.g. intelligence-worker) share the same broker and
# would silently consume + discard our 'vegetation.*' tasks as unregistered.
VEGETATION_QUEUE = 'vegetation'

# Celery configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3300,  # 30 min warning
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    worker_max_memory_per_child=1200000,  # 1.2 GB — recycle worker before cgroup OOM
    # Durability: do not lose in-flight messages on worker restart
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    # Queue isolation
    task_default_queue=VEGETATION_QUEUE,
    task_queues=(Queue(VEGETATION_QUEUE, routing_key=f'{VEGETATION_QUEUE}.#'),),
    task_default_exchange=VEGETATION_QUEUE,
    task_default_routing_key=f'{VEGETATION_QUEUE}.default',
)

# Periodic tasks
celery_app.conf.beat_schedule = {
    'vegetation.process_subscriptions': {
        'task': 'vegetation.process_subscriptions',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
    },
    'vegetation.cleanup_global_cache': {
        'task': 'vegetation.cleanup_global_cache',
        'schedule': crontab(hour=3, minute=0),  # Daily at 3 AM
    },
    'vegetation.reap_stuck_jobs': {
        'task': 'vegetation.reap_stuck_jobs',
        'schedule': crontab(minute='*/15'),  # Every 15 minutes
    },
    'vegetation.dispatch_lst': {
        'task': 'vegetation.dispatch_lst_for_active_parcels',
        'schedule': crontab(hour=4, minute=0, day_of_week=1),  # Weekly, Monday 4 AM
    },
}

if __name__ == '__main__':
    celery_app.start()
