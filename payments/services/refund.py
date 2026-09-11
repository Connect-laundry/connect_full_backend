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

from marketplace.services.audit import record_audit
from marketplace.services.customer_events import notify_customer_event
from ordering.models import Order

from ..models import Payment
from .paystack import PaystackService

logger = logging.getLogger(__name__)


class RefundError(Exception):
    """Refund could not be started. The message is safe to show an admin."""


class RefundOutcomeUnknown(RefundError):
    """Provider may have accepted the refund; callers must not retry blindly."""


def refund_payment(payment, *, amount=None, reason='', actor=None, request=None):
    """Reserve and start a refund without allowing duplicate submissions.

    The local state is claimed before the Paystack request. A clean rejection
    releases the claim; a timeout or malformed provider response leaves it in
    REFUND_PENDING for webhook settlement or manual reconciliation because
    retrying an unknown money-moving outcome could refund twice.
    """
    if amount is not None and amount <= 0:
        raise RefundError('Refund amount must be greater than zero.')

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.payment_method == Payment.Method.CASH:
            raise RefundError('Cash collections must be refunded outside Paystack and reconciled manually.')
        if locked.status == Payment.Status.REFUNDED:
            raise RefundError('This payment has already been refunded.')
        if locked.status == Payment.Status.REFUND_PENDING:
            raise RefundError('A refund is already in progress for this payment.')
        if locked.status != Payment.Status.SUCCESS:
            raise RefundError(
                f"Only a successful payment can be refunded (this one is '{locked.status}')."
            )
        if amount is not None and amount > locked.amount:
            raise RefundError('Refund amount cannot exceed the amount paid.')
        if amount is not None and amount != locked.amount:
            # The current ledger has one terminal REFUNDED state and reverses
            # the whole order settlement. Accepting a smaller provider refund
            # would therefore make local accounting disagree with Paystack.
            raise RefundError(
                'Partial refunds are not supported yet. Refund the full payment amount.'
            )

        locked.transition_to(Payment.Status.REFUND_PENDING)
        reference = locked.transaction_reference
        payment_amount = locked.amount
        order_id = locked.order_id

        record_audit(
            action='PAYMENT_REFUND_RESERVED',
            actor=actor,
            request=request,
            target_type='Payment',
            target_id=str(locked.id),
            target_repr=f'Refund reserved for {reference}',
            metadata={
                'amount': str(amount if amount is not None else payment_amount),
                'reason': reason or '',
                'order_id': str(order_id),
            },
        )

    response = PaystackService().refund_transaction(
        reference, amount=amount, reason=reason,
    )
    if not response.get('status'):
        message = response.get('message') or 'The payment provider rejected the refund.'
        if response.get('indeterminate'):
            logger.error(
                'Refund outcome unknown; left pending for reconciliation',
                extra={'payment_id': str(payment.pk), 'order_id': str(order_id)},
            )
            raise RefundOutcomeUnknown(message)

        with transaction.atomic():
            rejected = Payment.objects.select_for_update().get(pk=payment.pk)
            if rejected.status == Payment.Status.REFUND_PENDING:
                rejected.transition_to(Payment.Status.SUCCESS)
        raise RefundError(message)

    notify_customer_event(
        payment.user,
        'REFUND_INITIATED',
        payment=payment,
        dedup_key=f'refund_initiated_{payment.pk}',
    )

    record_audit(
        action='PAYMENT_REFUND_REQUESTED',
        actor=actor,
        request=request,
        target_type='Payment',
        target_id=str(payment.pk),
        target_repr=f'Refund requested for {reference}',
        metadata={
            'amount': str(amount if amount is not None else payment_amount),
            'reason': reason or '',
            'order_id': str(order_id),
        },
    )

    logger.info(
        'Refund requested',
        extra={'payment_id': str(payment.pk), 'order_id': str(order_id)},
    )
    payment.refresh_from_db()
    return payment

def mark_refund_settled(payment, *, request=None):
    """Apply a settled refund: payment REFUNDED, order REFUNDED, customer told.

    Idempotent — a repeated `refund.processed` webhook is a no-op.
    """
    if payment.payment_method == Payment.Method.CASH:
        raise RefundError('Cash collections must be refunded outside Paystack and reconciled manually.')
    if payment.status == Payment.Status.REFUNDED:
        return False

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status == Payment.Status.REFUNDED:
            return False

        locked.transition_to(Payment.Status.REFUNDED)

        order.payment_status = Order.PaymentStatus.REFUNDED
        order.save(update_fields=['payment_status', 'updated_at'])

        # The customer's money went back, so the laundry is no longer owed it.
        # Leaving the debt standing would pay a laundry for an order that was
        # refunded.
        from .settlement_service import SettlementService
        SettlementService.reverse_for_order(order, reason='Payment refunded')

        record_audit(
            action='PAYMENT_REFUND_SETTLED',
            actor=None,
            request=request,
            target_type='Payment',
            target_id=str(locked.id),
            target_repr=f'Refund settled for {locked.transaction_reference}',
            metadata={'amount': str(locked.amount), 'order_id': str(order.id)},
        )

        notify_customer_event(
            locked.user,
            'REFUND_COMPLETED',
            payment=locked,
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
