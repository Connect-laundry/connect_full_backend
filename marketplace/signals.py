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
logger = logging.getLogger(__name__)


# NOTE: Customer order-lifecycle notifications are handled exclusively by
# ordering/signals.py::trigger_order_notifications (via NotificationService,
# which deduplicates and applies push preferences). A second customer handler
# previously lived here and produced DUPLICATE notifications on every status
# change — it has been removed. This module retains only the admin-audience
# triggers below.


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

        pickup_dist_str = f"{instance.pickup_distance_km} km" if instance.pickup_distance_km is not None else "N/A"
        delivery_dist_str = f"{instance.delivery_distance_km} km" if instance.delivery_distance_km is not None else "N/A"
        promo_str = "YES" if instance.is_free_delivery_promo else "NO"
        funding_str = instance.promo_funding_source or "NONE"
        pm_display = getattr(instance, 'get_payment_method_display', lambda: instance.payment_method)()

        body = (
            f"Order {instance.order_no} placed ({pm_display}).\n"
            f"Total: GHS {instance.total_amount}\n"
            f"Pickup: {pickup_dist_str} (GHS {instance.pickup_fee})\n"
            f"Delivery: {delivery_dist_str} (GHS {instance.delivery_fee})\n"
            f"Free Delivery Promo: {promo_str} (Funded by: {funding_str})"
        )

        NotificationService.notify_admins(
            title=f"New booking: {instance.order_no} (GHS {instance.total_amount})",
            body=body,
            category='NEW_BOOKING',
            priority=Notification.Priority.NORMAL,
            type=Notification.Type.ORDER,
            related_order=instance,
            action_url=f'/admin/ordering/order/{instance.id}/change/',
            dedup_key=f'new_booking:{instance.id}',
        )
    _safe(_do)


@receiver(post_save, sender='laundries.Laundry')
def user_notify_free_delivery_promo(sender, instance, created, **kwargs):
    """
    When a laundry activates a free pickup/delivery promotion, send a
    deduplicated push notification to relevant customers and fans.
    """
    if not getattr(instance, 'free_delivery_promo_enabled', False):
        return

    def _do():
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        from django.contrib.auth import get_user_model
        User = get_user_model()
        from ordering.models import Order
        from laundries.models.favorite import Favorite

        promo_ver = str(instance.promo_start_at or getattr(instance, 'updated_at', None))

        favorited_user_ids = set(Favorite.objects.filter(laundry=instance).values_list('user_id', flat=True))
        past_customer_ids = set(Order.objects.filter(laundry=instance).values_list('user_id', flat=True))
        target_ids = favorited_user_ids | past_customer_ids

        for user_id in target_ids:
            try:
                user = User.objects.get(id=user_id)
                NotificationService.notify_user(
                    user=user,
                    title="Free Pickup & Delivery! 🚚",
                    body=f"{instance.name} is offering FREE pickup & delivery.",
                    category='PROMO_FREE_DELIVERY',
                    priority=Notification.Priority.NORMAL,
                    action_url=f"connect://laundry/{instance.id}",
                    dedup_key=f"promo_free_del:{instance.id}:{user.id}:{promo_ver}",
                    push=True,
                )
            except Exception:
                continue
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

    def _email():
        # Queue after commit so the task never races an uncommitted row. No
        # sync fallback — a broker outage must not block owner registration.
        from django.db import transaction
        from laundries.tasks import send_admin_new_laundry_email
        from utils.tasks import safe_task_delay

        laundry_id = str(instance.id)
        transaction.on_commit(
            lambda: safe_task_delay(send_admin_new_laundry_email, laundry_id)
        )
    _safe(_email)


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


@receiver(post_save, sender='laundries.Laundry')
def customer_notify_new_laundry_approved(sender, instance, created, **kwargs):
    """Broadcast a push to all customers when a laundry becomes active.

    Only fires on the first transition to ACTIVE so repeat saves (e.g. admin
    editing the record) don't re-send. Delivered via a Celery campaign task
    so a large user base doesn't block the HTTP request that approved it.
    """
    if getattr(instance, 'status', None) != 'ACTIVE':
        return

    # Only notify on the *first* activation (status field just became ACTIVE).
    # We use created=False to skip brand-new rows that start PENDING.
    if created:
        return

    def _broadcast():
        from django.db import transaction
        from marketplace.tasks import notify_new_laundry_to_customers
        from utils.tasks import safe_task_delay
        laundry_id = str(instance.id)
        transaction.on_commit(
            lambda: safe_task_delay(
                notify_new_laundry_to_customers, laundry_id, fallback_sync=False
            )
        )
    _safe(_broadcast)

