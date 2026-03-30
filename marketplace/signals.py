import logging

# pyre-ignore[missing-module]
from django.db.models.signals import post_save

# pyre-ignore[missing-module]
from django.dispatch import receiver

# pyre-ignore[missing-module]
from ordering.models import Order

# pyre-ignore[missing-module]
from ordering.services.order_state_machine import order_status_changed

# pyre-ignore[missing-module]
from marketplace.tasks import create_notification

logger = logging.getLogger(__name__)


def _safe_delay(task, **kwargs):
    """
    Safely dispatch a Celery task.
    If the broker (Redis) is unavailable, it falls back to synchronous execution
    so the process still completes and the notification is created.
    """
    try:
        task.delay(**kwargs)
    except Exception as e:
        logger.warning(
            f"Celery broker unavailable, falling back to synchronous execution for {
                task.name}: {e}"
        )
        try:
            # task.apply() runs the task synchronously in the current process
            task.apply(kwargs=kwargs)
        except Exception as sync_err:
            logger.error(f"Synchronous fallback also failed for {
                    task.name}: {sync_err}")


@receiver(post_save, sender=Order)
def notify_on_order_creation(sender, instance, created, **kwargs):
    """Notify the laundry owner when a new order is placed."""
    if created:
        owner = instance.laundry.owner
        _safe_delay(
            create_notification,
            user_id=str(owner.id),
            title="New Laundry Order",
            body=f"You have a new order {
                instance.order_no} from {
                instance.user.get_full_name()}.",
            notification_type="ORDER",
            related_order_id=str(instance.id),
        )


@receiver(order_status_changed)
def notify_on_status_change(sender, order, from_status, to_status, user, **kwargs):
    """Notify the customer on important order status updates and award loyalty points."""
    status_content = {
        Order.Status.CONFIRMED: {
            "title": "Order Confirmed",
            "body": f"Your order {order.order_no} has been accepted by the laundry.",
        },
        Order.Status.PICKED_UP: {
            "title": "Laundry Picked Up",
            "body": "The rider has picked up your laundry.",
        },
        Order.Status.IN_PROCESS: {
            "title": "Washing Started",
            "body": "Your laundry is now being processed.",
        },
        Order.Status.OUT_FOR_DELIVERY: {
            "title": "Out for Delivery",
            "body": "Your fresh laundry is on its way back to you!",
        },
        Order.Status.DELIVERED: {
            "title": "Laundry Delivered",
            "body": "Your order has been delivered successfully. Thank you for choosing Connect Laundry!",
        },
        Order.Status.COMPLETED: {
            "title": "Order Completed",
            "body": "Thank you for using Connect Laundry! You've earned loyalty points.",
        },
        Order.Status.CANCELLED: {
            "title": "Order Cancelled",
            "body": f"Order {order.order_no} has been cancelled.",
        },
        Order.Status.REJECTED: {
            "title": "Order Rejected",
            "body": "The laundry has rejected your order.",
        },
    }

    # 1. Dispatch Notification
    if to_status in status_content:
        content = status_content[to_status]
        _safe_delay(
            create_notification,
            user_id=str(order.user.id),
            title=content["title"],
            body=content["body"],
            notification_type="ORDER",
            related_order_id=str(order.id),
        )

    # 2. Award Loyalty Points on Completion
    if to_status == Order.Status.COMPLETED:
        try:
            from marketplace.models.loyalty import LoyaltyPoint, LoyaltyTransaction

            profile, _ = LoyaltyPoint.objects.get_or_create(user=order.user)

            # Award 10 points per order for now
            points_to_award = 10
            profile.points += points_to_award
            profile.total_earned += points_to_award
            profile.save()

            LoyaltyTransaction.objects.create(
                loyalty_profile=profile,
                amount=points_to_award,
                description=f"Points earned from Order {order.order_no}",
            )
            logger.info(f"Awarded {points_to_award} points to {
                    order.user.email}")
        except Exception as e:
            logger.error(f"Failed to award loyalty points for order {
                    order.id}: {e}")
