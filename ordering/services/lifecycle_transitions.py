"""The one entry point for an actor-driven order status change.

Wraps ``OrderStateMachine.transition`` with the payment consequences a
transition carries (refund on cancel/reject, COD and online-payment gates).
The lifecycle API and Django admin both use it, so staff cannot change an
order's status by a path that skips refunds, settlement rules, disputes or
operations alerts. It is not a second state machine: legality still comes
from ``OrderStateMachine``.

Results are returned, not raised, on purpose. ``refund_payment`` may leave a
REFUND_PENDING claim when Paystack's answer is unknown; that claim must be
committed (it prevents a double refund), so this function returns from inside
its transaction exactly as the view used to.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction

from ..models.base import Order
from .order_state_machine import OrderStateMachine

logger = logging.getLogger(__name__)

INVALID_TRANSITION = 'invalid_transition'
BLOCKED = 'blocked'


@dataclass
class TransitionResult:
    ok: bool
    order: Order
    error: str = ''
    message: str = ''
    http_status: int = 200
    data: dict = field(default_factory=dict)


def perform_transition(order_id, to_status, *, user, reason=None, metadata=None, request=None) -> TransitionResult:
    from payments.models import Payment

    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        payment = (
            Payment.objects.select_for_update()
            .filter(order_id=order.id)
            .first()
        )

        if (
            to_status == Order.Status.COMPLETED
            and order.payment_method == Order.PaymentMethod.CASH
            and order.payment_status != Order.PaymentStatus.PAID
        ):
            return TransitionResult(False, order, BLOCKED, 'Confirm cash collection before completing this COD order.',
                                    409)

        if to_status == Order.Status.CONFIRMED:
            accepts_before_payment = (
                order.payment_method == Order.PaymentMethod.CASH
                or order.pricing_mode == Order.PricingMode.CUSTOM_QUOTE
            )
            if (
                not accepts_before_payment
                and (payment is None or payment.status != Payment.Status.SUCCESS)
            ):
                return TransitionResult(False, order, BLOCKED,
                                        'Payment must be confirmed before this order can be accepted.', 409)

        # Legality before any money moves. A cancel the state machine would
        # refuse (clothes already picked up, delivered, completed) used to
        # start the customer's refund and only then fail with 400. The two
        # gates above keep their original precedence (API contract).
        if order.status != to_status and not OrderStateMachine.can_transition(order.status, to_status):
            return _invalid(order, to_status)

        if payment and payment.payment_method != Payment.Method.CASH and order.status != to_status:
            if to_status in {Order.Status.CANCELLED, Order.Status.REJECTED}:
                if payment.status == Payment.Status.SUCCESS:
                    from payments.services.refund import refund_payment, RefundError, RefundOutcomeUnknown
                    try:
                        refund_payment(
                            payment,
                            reason=reason or f'Order {to_status.lower()}',
                            actor=user,
                            request=request,
                        )
                    except (RefundError, RefundOutcomeUnknown) as e:
                        logger.error(
                            "Refund failed during order transition",
                            extra={"order_id": str(order.id), "error": str(e)},
                        )
                        return TransitionResult(
                            False, order, BLOCKED,
                            f"Unable to process refund: {str(e)}. Please contact support to cancel.", 409)
                elif payment.status == Payment.Status.PENDING and payment.transaction_reference:
                    from payments.services.paystack import PaystackService
                    from payments.services.refund import refund_payment
                    try:
                        verify_data = PaystackService().verify_transaction(payment.transaction_reference)
                        if verify_data.get('status') and verify_data.get('data', {}).get('status') == 'success':
                            payment.transition_to(Payment.Status.SUCCESS)
                            order.payment_status = Order.PaymentStatus.PAID
                            order.save(update_fields=['payment_status', 'updated_at'])
                            refund_payment(
                                payment,
                                reason=reason or f'Order {to_status.lower()}',
                                actor=user,
                                request=request,
                            )
                        else:
                            payment.transition_to(Payment.Status.FAILED)
                    except Exception as e:
                        logger.warning(f"Verification during cancel failed: {e}")
                        payment.transition_to(Payment.Status.FAILED)
                elif payment.status == Payment.Status.PENDING:
                    payment.transition_to(Payment.Status.FAILED)

        # The state machine reuses the surrounding transaction and locks
        # the order before validating the transition.
        updated_order, success = OrderStateMachine.transition(
            order_id=order.id,
            to_status=to_status,
            user=user,
            metadata=metadata,
            reason=reason,
        )
    if not success:
        return _invalid(order, to_status)
    return TransitionResult(True, updated_order)


def _invalid(order, to_status):
    return TransitionResult(False, order, INVALID_TRANSITION, 'Invalid state transition', 400,
                            {'current_status': order.status, 'target_status': to_status})
