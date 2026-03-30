# pyre-ignore[missing-module]
from django.dispatch import receiver
# pyre-ignore[missing-module]
from .services.order_state_machine import order_status_changed
import logging

logger = logging.getLogger(__name__)


@receiver(order_status_changed)
def log_order_status_change(
        sender,
        order,
        from_status,
        to_status,
        user,
        **kwargs):
    """
    Structured logging for every status transition.
    """
    logger.info(
        f"Lifecycle Transition: {
            order.order_no} | {from_status} -> {to_status}",
        extra={
            'order_id': str(
                order.id),
            'order_no': order.order_no,
            'from_status': from_status,
                'to_status': to_status,
                'user_id': str(
                    user.id) if user else None,
            'metadata': kwargs.get(
                'metadata',
                {})})


@receiver(order_status_changed)
def handle_coupon_usage(sender, order, from_status, to_status, **kwargs):
    """Increment used_count only after order confirmation."""
    # pyre-ignore[missing-module]
    from django.db import transaction
    if from_status == 'PENDING' and to_status == 'CONFIRMED' and order.coupon:
        with transaction.atomic():
            coupon = order.coupon
            # Real-time increment
            coupon.used_count += 1
            coupon.save()
            logger.info(
                f"Verified Coupon {
                    coupon.code} used for Order {
                    order.order_no}")
