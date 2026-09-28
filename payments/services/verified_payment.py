"""Apply a Paystack-verified successful charge to our records.

The consequences of "Paystack says this payment succeeded" must be identical
whichever door it came in through, because they decide what a laundry is
owed. This mirrors the customer verify endpoint (payments.views.PaymentVerifyView)
step for step and adds no money logic of its own: the amounts come from
``SettlementService.record_for_order``, the one Model A settlement calculation.

Callers verify with Paystack first, outside any transaction, then call this
inside ``transaction.atomic()``. Locks are taken in the platform-wide order
(Order, then Payment) so this cannot deadlock against a webhook.
"""
from __future__ import annotations

from django.utils import timezone

CHANNEL_METHODS = {
    'mobile_money': 'MOBILE_MONEY',
    'card': 'CARD',
    'bank': 'BANK_TRANSFER',
    'bank_transfer': 'BANK_TRANSFER',
    'transfer': 'BANK_TRANSFER',
}

APPLIED = 'applied'
ALREADY_DONE = 'already_done'
REJECTED = 'rejected'


def apply_verified_paystack_success(payment_id, gateway_data, *, actor=None, request=None,
                                    audit_action='PAYMENT_ADMIN_RECONCILED'):
    """Mark the payment paid, record the settlement, confirm the order.

    Returns ``(outcome, reason)``. Idempotent: a payment that is no longer
    PENDING is left untouched and reported as ``already_done``.
    """
    from marketplace.services.audit import record_audit
    from ordering.models import Order
    from ordering.services.order_state_machine import OrderStateMachine
    from payments.models import Payment
    from payments.services.settlement_service import SettlementService
    from payments.views import _sanitize_payment_response, _validate_verified_payment
    from payments.webhooks import _paystack_fee

    order_id = Payment.objects.filter(pk=payment_id).values_list('order_id', flat=True).first()
    if order_id is None:
        return REJECTED, 'Payment not found.'
    order = Order.objects.select_for_update().get(pk=order_id)
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.status != Payment.Status.PENDING:
        return ALREADY_DONE, f'Payment is already {payment.status}.'

    gateway_data = gateway_data if isinstance(gateway_data, dict) else {}
    is_valid, reason = _validate_verified_payment(payment, gateway_data)
    if not is_valid:
        payment.transition_to(Payment.Status.FAILED, save=False)
        payment.raw_response = _sanitize_payment_response(gateway_data, payment.transaction_reference)
        payment.save(update_fields=['status', 'raw_response', 'updated_at'])
        record_audit(
            action='PAYMENT_VERIFICATION_FAILED', actor=actor, request=request, target_type='Payment',
            target_id=str(payment.id), target_repr=f'Payment {payment.transaction_reference} Verification Failed',
            metadata={'reason': reason, 'amount': str(payment.amount), 'source': audit_action},
        )
        return REJECTED, reason

    payment.transition_to(Payment.Status.SUCCESS, save=False)
    payment.raw_response = _sanitize_payment_response(gateway_data, payment.transaction_reference)
    payment.paid_at = timezone.now()
    method = CHANNEL_METHODS.get(gateway_data.get('channel'))
    if method:
        payment.payment_method = method
    payment.save()

    order.payment_status = Order.PaymentStatus.PAID
    order.save(update_fields=['payment_status', 'updated_at'])

    SettlementService.record_for_order(
        order,
        processor_fee=_paystack_fee(gateway_data),
        settled_directly=payment.settled_directly,
    )
    OrderStateMachine.transition(order.id, Order.Status.CONFIRMED, user=actor)

    from admin_notifications.services.outbox import emit_payment_confirmed
    emit_payment_confirmed(order)

    record_audit(
        action=audit_action, actor=actor, request=request, target_type='Payment', target_id=str(payment.id),
        target_repr=f'Payment {payment.transaction_reference} Confirmed',
        metadata={'amount': str(payment.amount), 'order_id': str(order.id)},
    )
    return APPLIED, ''
