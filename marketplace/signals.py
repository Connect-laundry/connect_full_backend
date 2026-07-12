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
    """Dispatch a Celery task without ever raising (sync fallback on broker
    outage) — see utils.tasks.safe_task_delay."""
    from utils.tasks import safe_task_delay
    safe_task_delay(task, fallback_sync=True, **kwargs)


@receiver(post_save, sender=Order)
def notify_on_order_creation(sender, instance, created, **kwargs):
    """Notify the laundry owner when a new order is placed."""
    if created:
        owner = instance.laundry.owner
        _safe_delay(
            create_notification,
            user_id=str(owner.id),
            title="New Laundry Order",
            body=f"You have a new order {instance.order_no} from {instance.user.get_full_name()}.",
            notification_type='ORDER',
            related_order_id=str(instance.id)
        )


# NOTE: Customer order-lifecycle notifications are handled exclusively by
# ordering/signals.py::trigger_order_notifications (via NotificationService,
# which deduplicates and applies push preferences). A second customer handler
# previously lived here and produced DUPLICATE notifications on every status
# change — it has been removed. This module retains only the laundry-owner
# notification (on order creation) and the admin-audience triggers below.


# ---------------------------------------------------------------------------
# Admin Operations Center triggers
#
# Each domain event also produces an ADMIN-audience notification (surfaced in
# the admin bell). Handlers are best-effort: a notification failure must never
# break the core flow (registration, ordering, payments), so all are wrapped.
# dedup_key makes them idempotent across repeated saves.
# ---------------------------------------------------------------------------

def _safe(fn):
    try:
        fn()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Admin notification trigger failed", extra={"error": str(exc)})


@receiver(post_save, sender=Order)
def admin_notify_new_booking(sender, instance, created, **kwargs):
    if not created:
        return

    def _do():
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        NotificationService.notify_admins(
            title="New booking created",
            body=f"Order {instance.order_no} was placed.",
            category='NEW_BOOKING',
            priority=Notification.Priority.NORMAL,
            type=Notification.Type.ORDER,
            related_order=instance,
            action_url=f'/admin/ordering/order/{instance.id}/change/',
            dedup_key=f'new_booking:{instance.id}',
        )
    _safe(_do)


@receiver(post_save, sender=Order)
def admin_notify_order_cancelled(sender, instance, created, **kwargs):
    if created or instance.status != Order.Status.CANCELLED:
        return

    def _do():
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        NotificationService.notify_admins(
            title="Booking cancelled",
            body=f"Order {instance.order_no} was cancelled.",
            category='BOOKING_CANCELLED',
            priority=Notification.Priority.NORMAL,
            type=Notification.Type.ORDER,
            related_order=instance,
            action_url=f'/admin/ordering/order/{instance.id}/change/',
            dedup_key=f'booking_cancelled:{instance.id}',
        )
    _safe(_do)



@receiver(post_save, sender='users.User')
def admin_notify_new_user(sender, instance, created, **kwargs):
    if not created:
        return

    def _do():
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        is_owner = getattr(instance, 'role', None) == 'OWNER'
        NotificationService.notify_admins(
            title="New owner registered" if is_owner else "New user registered",
            body=f"{instance.get_full_name() or instance.email} ({instance.role}) signed up.",
            category='NEW_OWNER' if is_owner else 'NEW_USER',
            priority=Notification.Priority.HIGH if is_owner else Notification.Priority.NORMAL,
            action_url=f'/admin/users/user/{instance.id}/change/',
            dedup_key=f'new_user:{instance.id}',
        )
    _safe(_do)


@receiver(post_save, sender='laundries.Laundry')
def admin_notify_laundry_pending(sender, instance, created, **kwargs):
    if not created or getattr(instance, 'status', None) != 'PENDING':
        return

    def _do():
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        NotificationService.notify_admins(
            title="New laundry awaiting approval",
            body=f"'{instance.name}' was submitted and is pending approval.",
            category='LAUNDRY_PENDING',
            priority=Notification.Priority.HIGH,
            action_url=f'/admin/laundries/laundry/{instance.id}/change/',
            dedup_key=f'laundry_pending:{instance.id}',
        )
    _safe(_do)


@receiver(post_save, sender='payments.Payment')
def admin_notify_payment(sender, instance, created, **kwargs):
    status = instance.status
    if status not in ('SUCCESS', 'FAILED'):
        return

    def _do():
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        order_no = getattr(getattr(instance, 'order', None), 'order_no', '') or ''
        if status == 'SUCCESS':
            NotificationService.notify_admins(
                title="Payment received",
                body=f"Payment for order {order_no} succeeded ({instance.amount} {instance.currency}).",
                category='PAYMENT_SUCCESS',
                priority=Notification.Priority.NORMAL,
                type=Notification.Type.ORDER,
                related_order=getattr(instance, 'order', None),
                action_url=f'/admin/payments/payment/{instance.id}/change/',
                dedup_key=f'payment_success:{instance.id}',
            )
            if instance.user_id:
                NotificationService.notify_user(
                    instance.user,
                    title="Payment successful",
                    body=f"Your payment for order {order_no} was received. Thank you!",
                    category='PAYMENT_SUCCESS',
                    type=Notification.Type.ORDER,
                    related_order=getattr(instance, 'order', None),
                    dedup_key=f'payment_success_user:{instance.id}',
                )
                # Credit any recent campaign that drove this paid order.
                def _attribute():
                    from marketplace.services.campaign_service import CampaignService
                    CampaignService.attribute_conversion(
                        instance.user, order=getattr(instance, 'order', None),
                        value=instance.amount,
                    )
                _safe(_attribute)
        else:  # FAILED
            NotificationService.notify_admins(
                title="Payment failed",
                body=f"Payment for order {order_no} failed.",
                category='PAYMENT_FAILED',
                priority=Notification.Priority.HIGH,
                type=Notification.Type.ORDER,
                related_order=getattr(instance, 'order', None),
                action_url=f'/admin/payments/payment/{instance.id}/change/',
                dedup_key=f'payment_failed:{instance.id}',
            )
    _safe(_do)


@receiver(post_save, sender='laundries.Review')
def admin_notify_new_review(sender, instance, created, **kwargs):
    if not created:
        return

    def _do():
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        laundry_name = getattr(instance.laundry, 'name', '') if instance.laundry_id else ''
        NotificationService.notify_admins(
            title="New review submitted",
            body=f"{instance.rating}-star review for '{laundry_name}'.",
            category='NEW_REVIEW',
            priority=Notification.Priority.LOW,
            action_url=f'/admin/laundries/review/{instance.id}/change/',
            dedup_key=f'new_review:{instance.id}',
        )
    _safe(_do)
