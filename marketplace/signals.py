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

@receiver(post_save, sender=Order)
def notify_on_order_creation(sender, instance, created, **kwargs):
    """Notify the laundry owner when a new order is placed."""
    if created:
        owner = instance.laundry.owner
        create_notification.delay(
            user_id=owner.id,
            title="New Laundry Order",
            body=f"You have a new order {instance.order_no} from {instance.user.get_full_name()}.",
            notification_type='ORDER',
            related_order_id=instance.id
        )

@receiver(order_status_changed)
def notify_on_status_change(sender, order, from_status, to_status, user, **kwargs):
    """Notify the customer on important order status updates."""
    # Mapping of status to notification content
    status_content = {
        Order.Status.CONFIRMED: {
            "title": "Order Confirmed",
            "body": f"Your order {order.order_no} has been accepted by the laundry."
        },
        Order.Status.PICKED_UP: {
            "title": "Laundry Picked Up",
            "body": f"The rider has picked up your laundry from {order.address}."
        },
        Order.Status.OUT_FOR_DELIVERY: {
            "title": "Out for Delivery",
            "body": f"Your fresh laundry is on its way to you!"
        },
        Order.Status.DELIVERED: {
            "title": "Laundry Delivered",
            "body": "Your order has been delivered. Thank you for choosing Connect Laundry!"
        },
        Order.Status.CANCELLED: {
            "title": "Order Cancelled",
            "body": f"Order {order.order_no} has been cancelled."
        }
    }

    if to_status in status_content:
        content = status_content[to_status]
        # Notify the Customer
        create_notification.delay(
            user_id=order.user.id,
            title=content["title"],
            body=content["body"],
            notification_type='ORDER',
            related_order_id=order.id
        )
