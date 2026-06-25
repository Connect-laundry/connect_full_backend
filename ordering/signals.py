# pyre-ignore[missing-module]
from django.dispatch import receiver
# pyre-ignore[missing-module]
from .services.order_state_machine import order_status_changed
import logging

logger = logging.getLogger(__name__)

@receiver(order_status_changed)
def log_order_status_change(sender, order, from_status, to_status, user, **kwargs):
    """
    Structured logging for every status transition.
    """
    logger.info(
        f"Lifecycle Transition: {order.order_no} | {from_status} -> {to_status}",
        extra={
            'order_id': str(order.id),
            'order_no': order.order_no,
            'from_status': from_status,
            'to_status': to_status,
            'user_id': str(user.id) if user else None,
            'metadata': kwargs.get('metadata', {})
        }
    )

@receiver(order_status_changed)
def trigger_order_notifications(sender, order, from_status, to_status, **kwargs):
    """Canonical customer-facing order-lifecycle notification.

    This is the SINGLE source of customer order notifications — it persists the
    in-app record AND queues the push (preference- and quiet-hours-aware) via
    NotificationService. The per-(order, status) dedup_key makes it idempotent,
    so repeated saves or duplicate signal fires never produce duplicate records.

    A notification failure must never break order processing, hence the guard.
    """
    # pyre-ignore[missing-module]
    from marketplace.models import Notification
    from marketplace.services.notification_service import NotificationService

    status_messages = {
        'CONFIRMED': ("Order Confirmed", "Your order has been accepted and confirmed by the laundry."),
        'PICKED_UP': ("Order Picked Up", "A rider has picked up your laundry."),
        'IN_PROCESS': ("Washing Started", "Your laundry is now being processed."),
        'OUT_FOR_DELIVERY': ("Out for Delivery", "Your laundry is on its way back to you!"),
        'DELIVERED': ("Order Delivered", "Your laundry has been delivered successfully."),
        'COMPLETED': ("Order Complete", "Your order is complete. Thanks for choosing Connect Laundry!"),
        'CANCELLED': ("Order Cancelled", f"Your order has been cancelled. Reason: {order.cancellation_reason or 'No reason provided'}."),
        'REJECTED': ("Order Rejected", f"The laundry has rejected your order. Reason: {order.rejection_reason or 'No reason provided'}."),
    }

    if to_status not in status_messages:
        return

    title, message = status_messages[to_status]
    try:
        NotificationService.notify_user(
            order.user,
            title=title,
            body=message,
            type=Notification.Type.ORDER,
            category='ORDER',
            related_order=order,
            dedup_key=f'order_status:{order.id}:{to_status}',
        )
        logger.info(f"Customer order notification created for {order.user.email}: {title}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
            "Failed to create customer order notification",
            extra={'order_id': str(order.id), 'to_status': to_status, 'error': str(exc)},
        )

@receiver(order_status_changed)
def handle_coupon_usage(sender, order, from_status, to_status, **kwargs):
    """
    Coupon usage is counted atomically at order creation (see
    ordering/serializers/order.py, where CouponUsage is recorded and
    Coupon.current_usage is incremented under a row lock). This handler
    only logs confirmation; it must not mutate usage counters or it would
    double-count.
    """
    if from_status == 'PENDING' and to_status == 'CONFIRMED' and order.coupon:
        logger.info(
            f"Coupon {order.coupon.code} confirmed for Order {order.order_no}"
        )
