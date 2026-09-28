"""OutboxService: record admin alerts inside the business transaction.

Order and payment code call one ``emit_*`` function at the point where the
change becomes authoritative. That call:

- does nothing when ADMIN_ORDER_NOTIFICATIONS_ENABLED is off;
- runs in its own savepoint and swallows every error, so a notification
  problem (missing table mid-deploy, bad config, a bug here) can never reject,
  roll back or delay a booking or a payment;
- is idempotent through a database UNIQUE key per event, so retried requests,
  repeated webhooks, double taps and racing workers produce one event;
- builds a frozen snapshot of the order in the same transaction;
- schedules a dispatch after commit. Provider HTTP calls never run inside the
  business transaction.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging

from django.db import IntegrityError, transaction

from .. import conf
from ..models import AdminNotificationDelivery as Delivery
from ..models import AdminNotificationEvent as Event
from ..snapshot import PAYLOAD_VERSION, build_order_snapshot, money
from . import metrics

logger = logging.getLogger('admin_notifications')

TERMINAL_ORDER_STATUSES = {'CANCELLED', 'REJECTED', 'COMPLETED', 'DELIVERED'}
SCHEDULE_FIELDS = ('pickup_date', 'delivery_date')
PICKUP_FIELDS = ('pickup_address', 'pickup_lat', 'pickup_lng')
DELIVERY_FIELDS = ('delivery_address', 'delivery_lat', 'delivery_lng')
TRACKED_FIELDS = SCHEDULE_FIELDS + PICKUP_FIELDS + DELIVERY_FIELDS


def _never_raises(event_type):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(order, *args, **kwargs):
            if not conf.notifications_enabled():
                return None
            try:
                with transaction.atomic():
                    return fn(order, *args, **kwargs)
            except Exception as exc:
                logger.warning('Admin notification not recorded', exc_info=True,
                               extra={'event': metrics.EMIT_ERROR})
                metrics.alert(
                    metrics.EMIT_ERROR,
                    'Admin order notification could not be recorded (order unaffected)',
                    level='error', event_type=event_type, error_class=type(exc).__name__,
                    order_no=getattr(order, 'order_no', ''),
                )
                return None
        return wrapper
    return decorator


def _deliveries_for(event) -> list[Delivery]:
    rows = []
    if conf.whatsapp_enabled():
        for number in conf.whatsapp_recipients():
            rows.append(Delivery(event=event, channel=Delivery.Channel.WHATSAPP, recipient=number,
                                 provider='arkesel_whatsapp',
                                 dedup_key=f'{event.pk}:{Delivery.Channel.WHATSAPP}:{number}'))
    if conf.sms_enabled():
        for number in conf.sms_recipients():
            rows.append(Delivery(event=event, channel=Delivery.Channel.SMS, recipient=number,
                                 provider='arkesel_sms',
                                 dedup_key=f'{event.pk}:{Delivery.Channel.SMS}:{number}'))
    if conf.telegram_enabled():
        for chat_id in conf.telegram_chat_ids():
            rows.append(Delivery(event=event, channel=Delivery.Channel.TELEGRAM, recipient=chat_id,
                                 provider='telegram',
                                 dedup_key=f'{event.pk}:{Delivery.Channel.TELEGRAM}:{chat_id}'))
    return rows


def _record(order, event_type: str, key: str, *, extra=None, booking_payload=None):
    if Event.objects.filter(idempotency_key=key).exists():
        return None
    payload = build_order_snapshot(order, event_type=event_type, booking_payload=booking_payload, extra=extra)
    try:
        with transaction.atomic():
            event, created = Event.objects.get_or_create(
                idempotency_key=key,
                defaults={
                    'order_id': order.pk,
                    'event_type': event_type,
                    'payload': payload,
                    'payload_version': PAYLOAD_VERSION,
                    'environment': conf.environment(),
                },
            )
    except IntegrityError:
        # A concurrent emitter won the unique key between our get and create.
        return None
    if not created:
        return None
    rows = _deliveries_for(event)
    if rows:
        Delivery.objects.bulk_create(rows, ignore_conflicts=True)
    else:
        from django.utils import timezone
        Event.objects.filter(pk=event.pk).update(processed_at=timezone.now())
    metrics.record(metrics.CREATED, event_type=event_type, order_no=payload['order']['order_no'],
                   deliveries=len(rows))
    from .kick import kick_dispatch
    transaction.on_commit(kick_dispatch)
    return event


def _booking_payload(order):
    event = (
        Event.objects.filter(order_id=order.pk, event_type=Event.EventType.NEW_ORDER)
        .only('payload').first()
    )
    return event.payload if event else None


@_never_raises('NEW_ORDER')
def emit_new_order(order):
    """A real booking has been committed (any payment method, any pricing mode)."""
    return _record(order, Event.EventType.NEW_ORDER, f'NEW_ORDER:{order.pk}')


@_never_raises('PAYMENT_CONFIRMED')
def emit_payment_confirmed(order):
    """The backend has recorded the order as paid (verified Paystack or cash collected)."""
    from ordering.models import Order
    from payments.models import Payment

    booking = _booking_payload(order)
    if booking is None:
        return None
    current = Order.objects.only('payment_status').get(pk=order.pk)
    if current.payment_status != Order.PaymentStatus.PAID:
        return None
    payment = Payment.objects.filter(order_id=order.pk, status=Payment.Status.SUCCESS).first()
    if payment is None:
        return None
    amount = payment.amount_collected if payment.payment_method == Payment.Method.CASH and payment.amount_collected else payment.amount
    return _record(order, Event.EventType.PAYMENT_CONFIRMED, f'PAYMENT_CONFIRMED:{order.pk}',
                   extra={'amount': money(amount)}, booking_payload=booking)


@_never_raises('ORDER_CANCELLED')
def emit_order_cancelled(order, from_status=None, to_status='CANCELLED'):
    booking = _booking_payload(order)
    if booking is None:
        return None
    event_type = Event.EventType.ORDER_REJECTED if to_status == 'REJECTED' else Event.EventType.ORDER_CANCELLED
    reason = (order.rejection_reason if to_status == 'REJECTED' else order.cancellation_reason) or ''
    at = order.rejected_at if to_status == 'REJECTED' else order.cancelled_at
    return _record(order, event_type, f'{event_type}:{order.pk}', booking_payload=booking, extra={
        'from_status': from_status or '',
        'reason': reason.strip()[:300],
        'at': at.isoformat() if at else None,
    })


@_never_raises('ORDER_PRICE_FINALIZED')
def emit_order_price_finalized(order):
    """The authoritative final price was frozen (quote sent / weigh-in confirmed)."""
    from ordering.models import Order

    booking = _booking_payload(order)
    if booking is None:
        return None
    priced_at = Order.objects.only('priced_at').get(pk=order.pk).priced_at
    booking_money = booking.get('money') or {}
    previous = None if booking_money.get('awaiting_quote') else booking_money.get('total')
    key = f"ORDER_PRICE_FINALIZED:{order.pk}:{priced_at.isoformat() if priced_at else 'unpriced'}"
    return _record(order, Event.EventType.ORDER_PRICE_FINALIZED, key, booking_payload=booking,
                   extra={'previous_total': previous})


def _display(field, value) -> str:
    from ..rendering import fmt_dt
    if value is None or value == '':
        return ''
    if field in SCHEDULE_FIELDS:
        return fmt_dt(value.isoformat() if hasattr(value, 'isoformat') else value)
    return str(value).strip()


@_never_raises('ORDER_CHANGED')
def emit_order_changes(order, changes: dict):
    """Pickup/delivery time or place changed after the booking alert went out.

    ``changes`` maps field name -> (old, new) and only holds real changes.
    """
    if not changes or order.status in TERMINAL_ORDER_STATUSES:
        return None
    booking = _booking_payload(order)
    if booking is None:
        return None
    if any(f in changes for f in SCHEDULE_FIELDS):
        event_type = Event.EventType.ORDER_RESCHEDULED
    elif any(f in changes for f in PICKUP_FIELDS):
        event_type = Event.EventType.PICKUP_LOCATION_CHANGED
    else:
        event_type = Event.EventType.DELIVERY_LOCATION_CHANGED

    labels = {
        'pickup_date': 'Pickup time', 'delivery_date': 'Delivery time',
        'pickup_address': 'Pickup address', 'delivery_address': 'Delivery address',
    }
    listed = []
    for field in TRACKED_FIELDS:
        if field not in changes or field.endswith(('_lat', '_lng')):
            continue
        old, new = changes[field]
        listed.append({'field': field, 'label': labels[field], 'old': _display(field, old),
                       'new': _display(field, new)})
    for leg in ('pickup', 'delivery'):
        if f'{leg}_lat' in changes or f'{leg}_lng' in changes:
            old_lat, new_lat = changes.get(f'{leg}_lat', (getattr(order, f'{leg}_lat'),) * 2)
            old_lng, new_lng = changes.get(f'{leg}_lng', (getattr(order, f'{leg}_lng'),) * 2)
            listed.append({
                'field': f'{leg}_pin', 'label': f'{leg.capitalize()} map pin',
                'old': f'{old_lat},{old_lng}' if old_lat is not None and old_lng is not None else '',
                'new': f'{new_lat},{new_lng}' if new_lat is not None and new_lng is not None else '',
            })
    digest = hashlib.sha256(
        json.dumps([listed, order.updated_at.isoformat() if order.updated_at else ''], sort_keys=True,
                   default=str).encode('utf-8')
    ).hexdigest()[:24]
    return _record(order, event_type, f'{event_type}:{order.pk}:{digest}', booking_payload=booking,
                   extra={'changes': listed})
