"""Celery task definitions for asynchronous notification processing.

Supports horizontal scaling across Celery workers while maintaining
idempotency and transactional integrity via SELECT FOR UPDATE SKIP LOCKED.
"""
from __future__ import annotations

import logging
from celery import shared_task

logger = logging.getLogger('admin_notifications')


@shared_task(
    name='admin_notifications.tasks.dispatch_notifications',
    bind=True,
    max_retries=3,
    soft_time_limit=30,
    time_limit=60,
    acks_late=True,
)
def dispatch_notifications_task(self, limit=None, channel=None):
    """Asynchronously claims and dispatches due notifications from the outbox."""
    from .services.dispatcher import dispatch_due
    try:
        result = dispatch_due(limit=limit, channel=channel)
        return {
            'claimed': result.claimed,
            'accepted': result.accepted,
            'retry': result.retry,
            'failed': result.failed,
            'unknown': result.unknown,
            'dead': result.dead,
            'blocked': result.blocked,
        }
    except Exception as exc:
        logger.warning('Celery dispatch_notifications task failed: %s', exc, exc_info=True)
        raise self.retry(exc=exc, countdown=15)


@shared_task(
    name='admin_notifications.tasks.reconcile_sms_reports',
    bind=True,
    soft_time_limit=60,
    time_limit=90,
    acks_late=True,
)
def reconcile_sms_reports_task(self):
    """Reconciles pending SMS delivery statuses with Arkesel."""
    from .services.maintenance import reconcile_sms_reports
    try:
        return reconcile_sms_reports()
    except Exception as exc:
        logger.warning('Celery reconcile_sms_reports task failed: %s', exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)


@shared_task(
    name='admin_notifications.tasks.purge_old_payloads',
    bind=True,
    soft_time_limit=60,
    time_limit=90,
    acks_late=True,
)
def purge_old_payloads_task(self):
    """Scrubs sensitive customer data from settled events past retention window."""
    from .services.maintenance import purge_old_payloads
    try:
        return purge_old_payloads()
    except Exception as exc:
        logger.warning('Celery purge_old_payloads task failed: %s', exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
