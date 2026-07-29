"""Refund orchestration.

Single entry point for refunding a settled payment so the admin API, any
future owner-facing action and the webhook handler all move the same state in
the same order.

Paystack settles refunds asynchronously: `POST /refund` only *accepts* the
request. The payment therefore moves to REFUND_PENDING here and reaches
REFUNDED when the `refund.processed` webhook lands.
"""
import logging

from django.db import transaction

from marketplace.models import Notification
from marketplace.services.audit import record_audit
from marketplace.services.notification_service import NotificationService
from ordering.models import Order

from ..models import Payment
from .paystack import PaystackService

logger = logging.getLogger(__name__)


class RefundError(Exception):
    """Refund could not be started. The message is safe to show an admin."""


def refund_payment(payment, *, amount=None, reason='', actor=None, request=None):
    """Start a refund for ``payment``.

    Returns the refreshed payment. Raises :class:`RefundError` when the
    payment is not in a refundable state or the gateway rejects the request.
    """
    if payment.status == Payment.Status.REFUNDED:
        raise RefundError('This payment has already been refunded.')
    if payment.status == Payment.Status.REFUND_PENDING:
        raise RefundError('A refund is already in progress for this payment.')
    if payment.status != Payment.Status.SUCCESS:
        raise RefundError(
            f"Only a successful payment can be refunded (this one is '{payment.status}')."
        )

    if amount is not None:
        if amount <= 0:
            raise RefundError('Refund amount must be greater than zero.')
        if amount > payment.amount:
            raise RefundError('Refund amount cannot exceed the amount paid.')

    response = PaystackService().refund_transaction(
        payment.transaction_reference, amount=amount, reason=reason,
    )
    if not response.get('status'):
        raise RefundError(
            response.get('message') or 'The payment provider rejected the refund.'
        )

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        # Re-check under the lock: a concurrent refund may have won the race.
        if locked.status != Payment.Status.SUCCESS:
            raise RefundError('This payment is no longer refundable.')

        locked.transition_to(Payment.Status.REFUND_PENDING)

        record_audit(
            action='PAYMENT_REFUND_REQUESTED',
            actor=actor,
            request=request,
            target_type='Payment',
            target_id=str(locked.id),
            target_repr=f'Refund requested for {locked.transaction_reference}',
            metadata={
                'amount': str(amount if amount is not None else locked.amount),
                'reason': reason or '',
                'order_id': str(locked.order_id),
            },
        )

    logger.info(
        'Refund requested',
        extra={'payment_id': str(payment.pk), 'order_id': str(payment.order_id)},
    )
    payment.refresh_from_db()
    return payment


def mark_refund_settled(payment, *, request=None):
    """Apply a settled refund: payment REFUNDED, order REFUNDED, customer told.

    Idempotent — a repeated `refund.processed` webhook is a no-op.
    """
    if payment.status == Payment.Status.REFUNDED:
        return False

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status == Payment.Status.REFUNDED:
            return False

        locked.transition_to(Payment.Status.REFUNDED)

        order = locked.order
        order.payment_status = Order.PaymentStatus.REFUNDED
        order.save(update_fields=['payment_status', 'updated_at'])

        record_audit(
            action='PAYMENT_REFUND_SETTLED',
            actor=None,
            request=request,
            target_type='Payment',
            target_id=str(locked.id),
            target_repr=f'Refund settled for {locked.transaction_reference}',
            metadata={'amount': str(locked.amount), 'order_id': str(order.id)},
        )

        NotificationService.notify_user(
            user=locked.user,
            title='Refund Processed',
            body=f'Your refund of GHS {locked.amount} for order {order.order_no} has been processed.',
            type=Notification.Type.ORDER,
            category='PAYMENT_REFUNDED',
            related_order=order,
            dedup_key=f'refund_settled_{locked.id}',
        )

    return True


def mark_refund_failed(payment, *, request=None):
    """Roll a pending refund back to SUCCESS so it can be retried."""
    if payment.status != Payment.Status.REFUND_PENDING:
        return False

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.Status.REFUND_PENDING:
            return False

        locked.transition_to(Payment.Status.SUCCESS)

        record_audit(
            action='PAYMENT_REFUND_FAILED',
            actor=None,
            request=request,
            target_type='Payment',
            target_id=str(locked.id),
            target_repr=f'Refund failed for {locked.transaction_reference}',
            metadata={'order_id': str(locked.order_id)},
        )

    logger.warning('Refund failed at the gateway', extra={'payment_id': str(payment.pk)})
    return True
