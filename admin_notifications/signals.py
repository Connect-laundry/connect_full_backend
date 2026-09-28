"""Order hooks that are not tied to one call site.

- Cancellation/rejection: every status change goes through
  ``OrderStateMachine.transition``, which sends ``order_status_changed``
  inside its transaction.
- Schedule/location changes: no endpoint edits pickup/delivery time or place
  today, but riders are dispatched by hand, so a stale address is dangerous
  whenever one appears (a future reschedule endpoint, an admin or shell edit).
  The values loaded with the instance are compared with the saved ones; only
  a real change emits, and only for orders that already had a NEW_ORDER alert.
"""
from decimal import Decimal, InvalidOperation

from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from ordering.models import Order
from ordering.services.order_state_machine import order_status_changed

from . import conf
from .services.outbox import TRACKED_FIELDS, emit_order_cancelled, emit_order_changes

_ATTR = '_admin_notification_tracked'


@receiver(order_status_changed, dispatch_uid='admin_notifications.order_status_changed')
def notify_admins_of_cancellation(sender, order, from_status, to_status, **kwargs):
    if to_status in (Order.Status.CANCELLED, Order.Status.REJECTED):
        emit_order_cancelled(order, from_status=from_status, to_status=to_status)


def _loaded_values(instance) -> dict:
    # __dict__ only: reading a deferred field here would trigger a query.
    data = instance.__dict__
    return {name: data[name] for name in TRACKED_FIELDS if name in data}


@receiver(post_init, sender=Order, dispatch_uid='admin_notifications.order_post_init')
def remember_tracked_values(sender, instance, **kwargs):
    instance.__dict__[_ATTR] = _loaded_values(instance)


def _normal(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return Decimal(stripped).normalize() if stripped.replace('.', '', 1).lstrip('-').isdigit() else stripped
        except InvalidOperation:
            return stripped
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        return Decimal(str(value)).normalize()
    return value


@receiver(post_save, sender=Order, dispatch_uid='admin_notifications.order_post_save')
def notify_admins_of_order_changes(sender, instance, created, raw=False, update_fields=None, **kwargs):
    before = instance.__dict__.get(_ATTR)
    after = _loaded_values(instance)
    instance.__dict__[_ATTR] = after
    if created or raw or before is None or not conf.notifications_enabled():
        return
    if update_fields is not None and not set(update_fields) & set(TRACKED_FIELDS):
        return
    changes = {
        name: (before[name], after[name])
        for name in TRACKED_FIELDS
        if name in before and name in after and _normal(before[name]) != _normal(after[name])
    }
    if changes:
        emit_order_changes(instance, changes)
