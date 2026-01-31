"""
Celery application configuration for Vegetation Prime module.
"""

import os
from celery import Celery
from celery.schedules import crontab

# Create Celery app
celery_app = Celery(
    'vegetation_prime',
    broker=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
    include=['app.tasks']
)

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
)

# Periodic tasks (optional - for scheduled processing)
celery_app.conf.beat_schedule = {
    'vegetation.process_subscriptions': {
        'task': 'vegetation.process_subscriptions',
        'schedule': crontab(hour=2, minute=0), # Daily at 2 AM
    },
}

if __name__ == '__main__':
    celery_app.start()
