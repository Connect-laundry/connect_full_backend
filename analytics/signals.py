"""Server-side analytics capture.

Authoritative funnel events (order creation, successful payment) are emitted
here so dashboards don't depend solely on client-reported events. All handlers
are best-effort and must never break the originating business flow.
"""
import logging

# pyre-ignore[missing-module]
from django.db.models.signals import post_save
# pyre-ignore[missing-module]
from django.dispatch import receiver

from .services import AnalyticsService

logger = logging.getLogger(__name__)


@receiver(post_save, sender='ordering.Order')
def capture_order_created(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        AnalyticsService.record_server_event(
            'ORDER_CREATED',
            user=getattr(instance, 'user', None),
            event_data={
                'order_id': str(instance.id),
                'order_no': getattr(instance, 'order_no', ''),
                'total_amount': str(getattr(instance, 'total_amount', '') or ''),
                'laundry_id': str(getattr(instance, 'laundry_id', '') or ''),
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("capture_order_created failed", extra={'error': str(exc)})


@receiver(post_save, sender='payments.Payment')
def capture_payment_event(sender, instance, created, **kwargs):
    status = getattr(instance, 'status', None)
    if status not in ('SUCCESS', 'FAILED'):
        return
    try:
        AnalyticsService.record_server_event(
            'PAYMENT_SUCCESS' if status == 'SUCCESS' else 'PAYMENT_FAILED',
            user=getattr(instance, 'user', None),
            event_data={
                'payment_id': str(instance.id),
                'order_id': str(getattr(instance, 'order_id', '') or ''),
                'amount': str(getattr(instance, 'amount', '') or ''),
                'currency': getattr(instance, 'currency', ''),
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("capture_payment_event failed", extra={'error': str(exc)})
