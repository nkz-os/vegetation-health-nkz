"""
Celery application configuration for Vegetation Prime module.
"""

import os
from celery import Celery
from celery.schedules import crontab
from kombu import Queue

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
}

if __name__ == '__main__':
    celery_app.start()
