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
    """
    Hook for triggering push notifications and emails.
    Creates internal Notification records for the recipient.
    """
    # pyre-ignore[missing-module]
    from marketplace.models import Notification
    
    # Map status to user-friendly messages
    status_messages = {
        'CONFIRMED': ("Order Confirmed", "Your order has been accepted and confirmed by the laundry."),
        'PICKED_UP': ("Order Picked Up", "A rider has picked up your laundry."),
        'IN_PROCESS': ("Washing Started", "Your laundry is now being processed."),
        'OUT_FOR_DELIVERY': ("Out for Delivery", "Your laundry is on its way back to you!"),
        'DELIVERED': ("Order Delivered", "Your laundry has been delivered successfully."),
        'CANCELLED': ("Order Cancelled", f"Your order has been cancelled. Reason: {order.cancellation_reason or 'No reason provided'}."),
        'REJECTED': ("Order Rejected", f"The laundry has rejected your order. Reason: {order.rejection_reason or 'No reason provided'}.")
    }

    if to_status in status_messages:
        title, message = status_messages[to_status]
        
        # Create persistent notification
        Notification.objects.create(
            recipient=order.user,
            title=title,
            message=message,
            type=Notification.Type.ORDER,
            related_order=order
        )
        
        # Log for asynchronous dispatch (e.g. Firebase Push)
        logger.info(f"Notification record created for {order.user.email}: {title}")

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
            logger.info(f"Verified Coupon {coupon.code} used for Order {order.order_no}")
