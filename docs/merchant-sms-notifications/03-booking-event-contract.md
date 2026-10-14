# 03 — Booking Event Contract & Idempotency Rules

## 1. Authoritative Event Definitions

SIMAME defines strict rules regarding when a merchant is notified during an order's lifecycle. A key requirement is avoiding premature alerts when customers open payment checkout but abandon the session.

| Order Type | Payment Method | Authoritative Notification Event | Triggering Code Path | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **Cash on Delivery (COD)** | `CASH` | `NEW_ORDER` | `ordering.views.booking.BookingCreateView` | Order is finalized immediately upon booking creation. |
| **Custom Quote Order** | Any (`CUSTOM_QUOTE`) | `NEW_ORDER` | `ordering.views.booking.BookingCreateView` | Laundry needs to inspect weight/items before final price is set. |
| **Online Card Payment** | `CARD` | `PAYMENT_CONFIRMED` | `payments.views.webhooks.paystack_webhook` | Customer must complete Paystack 3DS authorization. |
| **Mobile Money (MoMo)** | `MOBILE_MONEY` | `PAYMENT_CONFIRMED` | `payments.views.webhooks.paystack_webhook` | Customer must authorize USSD prompt on their handset. |
| **Order Cancellation** | Any | `ORDER_CANCELLED` | `ordering.views.lifecycle.OrderCancelView` | Only sent if the laundry was previously alerted. |
| **Schedule / Address Change**| Any | `ORDER_RESCHEDULED`, `PICKUP_LOCATION_CHANGED`, `DELIVERY_LOCATION_CHANGED` | Reschedule / location update views | Only sent if the laundry was previously alerted. |

---

## 2. Pay-First Classification Logic

In `admin_notifications/services/merchant.py`:
```python
def is_pay_first(order) -> bool:
    """True when the order requires upfront payment before fulfillment begins."""
    return order.payment_method != 'CASH' and order.pricing_mode != 'CUSTOM_QUOTE'

def template_for(event_type: str, order) -> str | None:
    E = Event.EventType
    if event_type == E.NEW_ORDER:
        return None if is_pay_first(order) else BOOKING
    if event_type == E.PAYMENT_CONFIRMED:
        return BOOKING if is_pay_first(order) else None
    if event_type == E.ORDER_CANCELLED:
        return CANCELLED
    if event_type in (E.ORDER_RESCHEDULED, E.PICKUP_LOCATION_CHANGED, E.DELIVERY_LOCATION_CHANGED):
        return CHANGED
    return None
```

---

## 3. Database Idempotency & De-duplication Rules

To protect against duplicate payment webhooks, retries, and concurrent API requests, uniqueness is enforced across multiple database layers:

1. **Event Idempotency Key**:
   - `AdminNotificationEvent.idempotency_key = f'{event_type}:{order.pk}'`
   - Backed by `unique=True` on the `idempotency_key` column.
   - Any second attempt to record the same event type for the same order returns the existing event immediately.
2. **Delivery De-duplication Key**:
   - `AdminNotificationDelivery.dedup_key = f'{event.pk}:{audience}:{laundry.pk}'`
   - Enforced by database unique constraint:
     ```python
     UniqueConstraint(
         fields=['event', 'channel', 'recipient', 'audience'],
         name='uniq_admin_notif_delivery_recipient'
     )
     ```
3. **Provider Message ID Tracking**:
   - `AdminNotificationProviderMessage.provider_message_id`:
     ```python
     UniqueConstraint(
         fields=['provider', 'provider_message_id'],
         name='uniq_admin_notif_provider_msg'
     )
     ```
   - Prevents duplicate delivery receipts from being credited or processed multiple times.

---

## 4. In-Flight Sequencing Protection

When a customer rapidly places an order and immediately cancels it, traditional message queues can experience race conditions where the cancellation SMS arrives *before* the new booking SMS.

SIMAME prevents this using an in-flight check during `_claim`:
```python
earlier_in_flight = Delivery.objects.filter(
    event__order_id=OuterRef('event__order_id'),
    channel=OuterRef('channel'),
    recipient=OuterRef('recipient'),
    event__created_at__lt=OuterRef('event__created_at'),
    status__in=[S.PENDING, S.RETRY, S.SENDING],
)

qs = Delivery.objects.filter(
    status__in=[S.PENDING, S.RETRY], next_attempt_at__lte=now,
).filter(sendable).filter(~Exists(earlier_in_flight))
```
If an earlier event for the same order and recipient is still in `PENDING`, `RETRY`, or `SENDING` state, the subsequent event is automatically held until the prior event reaches a terminal status (`SUBMITTED`, `SENT`, `FAILED`, `DEAD`, or `BLOCKED`).
