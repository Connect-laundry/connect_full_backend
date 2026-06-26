"""Analytics maintenance tasks.

A daily retention sweep keeps the raw-event table bounded. Heavier rollup /
materialized-view aggregation is layered on in later analytics phases; this
module gives the beat scheduler a safe, idempotent job to start from.
"""
import logging

# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task(name="analytics.tasks.prune_old_events")
def prune_old_events():
    """Delete raw events older than ANALYTICS_RETENTION_DAYS (default 180)."""
    from .models import AnalyticsEvent
    days = getattr(settings, 'ANALYTICS_RETENTION_DAYS', 180)
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = AnalyticsEvent.objects.filter(created_at__lt=cutoff).delete()
    if deleted:
        logger.info("Pruned old analytics events", extra={"deleted": deleted})
    return deleted
