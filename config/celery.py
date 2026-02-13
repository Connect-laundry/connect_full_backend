import os
# pyre-ignore[missing-module]
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Production Reliability Settings
app.conf.update(
    # Redis visibility timeout (1 hour) to prevent premature task re-delivery
    broker_transport_options={'visibility_timeout': 3600},
    # Ensure worker sends events for observability
    worker_send_task_events=True,
    task_send_sent_event=True,
    # Resource management
    task_soft_time_limit=30,
    task_time_limit=60,
    worker_prefetch_multiplier=1, # Fair distribution
    task_acks_late=True, # Ack after completion (requires idempotency)
    task_reject_on_worker_lost=True,
)

# Load task modules from all registered Django apps.
app.autodiscover_tasks()
