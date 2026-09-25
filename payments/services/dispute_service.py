"""
Customer disputes ("Report a problem") on delivered orders.

An OPEN dispute holds the order's settlement. The hold itself lives in
`settlement_service.release_blocker`, the single check every release path
already runs, so there is no second hold to keep in sync.

Opening a dispute and releasing a settlement both lock the settlement row
first. Whichever gets the lock first wins cleanly: a dispute opened first
blocks the release; a release that commits first leaves nothing HELD, and
the dispute is refused.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from marketplace.services.audit import record_audit

from ..models import OrderDispute, OrderSettlement, Payment

logger = logging.getLogger(__name__)

DISPUTABLE_ORDER_STATUSES = ('OUT_FOR_DELIVERY', 'DELIVERED', 'COMPLETED')


class DisputeError(Exception):
    """The dispute action was refused. The message is safe to show the caller."""


def open_dispute_for(order):
    return OrderDispute.objects.filter(order=order, status=OrderDispute.Status.OPEN).first()


def can_open_dispute(order):
    """Cheap, unlocked check for UI hints. The locked check in open_dispute decides."""
    if order.status not in DISPUTABLE_ORDER_STATUSES:
        return False
    if open_dispute_for(order) is not None:
        return False
    return OrderSettlement.objects.filter(order=order, status=OrderSettlement.Status.HELD).exists()


def open_dispute(order, customer, reason, details='', request=None):
    """
    Open a dispute for the customer's own order.

    Returns ``(dispute, created)``. A repeat report returns the existing open
    dispute with ``created=False`` and no further side effects.
    """
    from ordering.models import Order

    if reason not in OrderDispute.Reason.values:
        raise DisputeError('Choose what went wrong with your order.')

    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if locked_order.user_id != customer.id:
            # Callers already scope orders to the customer; this is the backstop.
            raise DisputeError('You can only report a problem with your own order.')

        existing = open_dispute_for(locked_order)
        if existing is not None:
            return existing, False

        settlement = (
            OrderSettlement.objects.select_for_update().filter(order=locked_order).first()
        )
        if locked_order.status not in DISPUTABLE_ORDER_STATUSES:
            raise DisputeError("You can report a problem once your order is on its way back to you.")
        if settlement is None or settlement.status != OrderSettlement.Status.HELD:
            raise DisputeError(
                "This order's payment has already been released to the laundry. "
                "Please contact support and we'll look into it."
            )

        try:
            with transaction.atomic():
                dispute = OrderDispute.objects.create(
                    order=locked_order,
                    raised_by=customer,
                    reason=reason,
                    details=(details or '')[:2000],
                )
        except IntegrityError:
            # Lost a race with an identical report on the partial unique index.
            return open_dispute_for(locked_order), False

        record_audit(
            action='ORDER_DISPUTE_OPENED',
            actor=customer,
            request=request,
            target_type='OrderDispute',
            target_id=str(dispute.id),
            target_repr=f'Dispute on {locked_order.order_no}',
            metadata={
                'order_id': str(locked_order.id),
                'settlement_id': str(settlement.id),
                'reason': reason,
            },
        )
        transaction.on_commit(lambda: _notify_opened(dispute))

    logger.info(
        "Order dispute opened; settlement held",
        extra={'order_id': str(order.pk), 'dispute_id': str(dispute.id)},
    )
    return dispute, True


def _require_staff(actor):
    if not (getattr(actor, 'is_staff', False) or getattr(actor, 'role', '') == 'ADMIN'):
        raise DisputeError('Only support staff can resolve a dispute.')


def _lock_open(dispute):
    # of=('self',): select_related would otherwise also lock the order row.
    return (
        OrderDispute.objects.select_for_update(of=('self',))
        .select_related('order')
        .get(pk=dispute.pk)
    )


def resolve_release(dispute, actor, note='', request=None):
    """Support found in the laundry's favour: lift the hold and release the money."""
    _require_staff(actor)
    from .settlement_service import SettlementService

    with transaction.atomic():
        locked = _lock_open(dispute)
        if locked.status != OrderDispute.Status.OPEN:
            return locked, False

        settlement = (
            OrderSettlement.objects.select_for_update().filter(order=locked.order).first()
        )
        _close(locked, OrderDispute.Status.RESOLVED_RELEASED, actor, note)
        released = None
        if settlement is not None and settlement.status == OrderSettlement.Status.HELD:
            # Re-runs every release re-check (refund in flight, cancellation,
            # laundry match); the dispute is no longer OPEN so it doesn't block.
            released = SettlementService.release_for_order(locked.order, confirmed=True)
        if released is None:
            # Nothing could be released automatically (already moved, or a
            # re-check failed). Don't guess: hand it to finance.
            locked.status = OrderDispute.Status.MANUAL_REVIEW
            locked.save(update_fields=['status', 'updated_at'])

        record_audit(
            action='ORDER_DISPUTE_RESOLVED',
            actor=actor,
            request=request,
            target_type='OrderDispute',
            target_id=str(locked.id),
            target_repr=f'Dispute on {locked.order.order_no}',
            metadata={
                'order_id': str(locked.order_id),
                'outcome': locked.status,
                'settlement_status': getattr(settlement, 'status', None),
                'note': note or '',
            },
        )
        if locked.status == OrderDispute.Status.MANUAL_REVIEW:
            transaction.on_commit(lambda: _alert_finance(locked, settlement))
        else:
            transaction.on_commit(lambda: _notify_resolved(locked))
    return locked, True


def resolve_refund(dispute, actor, note='', request=None):
    """
    Support found in the customer's favour: refund through the existing
    refund workflow. The settlement stays HELD (the release re-check blocks
    REFUND_PENDING) and is reversed when Paystack confirms the refund.

    If the money is no longer HELD -- released, scheduled, or already paid --
    nothing is refunded or reversed here; the dispute goes to manual
    financial review instead.
    """
    _require_staff(actor)
    from .refund import RefundError, RefundOutcomeUnknown, refund_payment

    with transaction.atomic():
        locked = _lock_open(dispute)
        if locked.status != OrderDispute.Status.OPEN:
            return locked, False
        settlement = (
            OrderSettlement.objects.select_for_update().filter(order=locked.order).first()
        )
        payment = Payment.objects.filter(order=locked.order).first()
        if settlement is None or settlement.status != OrderSettlement.Status.HELD or payment is None:
            _close(locked, OrderDispute.Status.MANUAL_REVIEW, actor, note)
            _audit_resolution(locked, actor, request, note, settlement, 'money no longer held')
            transaction.on_commit(lambda: _alert_finance(locked, settlement))
            return locked, True

    # Outside the lock: refund_payment calls Paystack and manages its own
    # locking. The dispute stays OPEN (and the money HELD) until it succeeds.
    try:
        refund_payment(
            payment,
            reason=f'Dispute {locked.id}: {note}'.strip(),
            actor=actor,
            request=request,
        )
    except RefundOutcomeUnknown:
        with transaction.atomic():
            locked = _lock_open(dispute)
            if locked.status == OrderDispute.Status.OPEN:
                _close(locked, OrderDispute.Status.MANUAL_REVIEW, actor, note)
                _audit_resolution(locked, actor, request, note, settlement, 'refund outcome unknown')
                transaction.on_commit(lambda: _alert_finance(locked, settlement))
        return locked, True
    except RefundError as exc:
        # Clean rejection: nothing moved. Leave the dispute OPEN (money held).
        raise DisputeError(f'Refund could not be started: {exc}') from exc

    with transaction.atomic():
        locked = _lock_open(dispute)
        if locked.status != OrderDispute.Status.OPEN:
            return locked, False
        _close(locked, OrderDispute.Status.RESOLVED_REFUNDED, actor, note)
        _audit_resolution(locked, actor, request, note, settlement, None)
        transaction.on_commit(lambda: _notify_resolved(locked))
    return locked, True


def _close(dispute, status, actor, note):
    dispute.status = status
    dispute.resolved_by = actor
    dispute.resolved_at = timezone.now()
    dispute.resolution_note = (note or '')[:2000]
    dispute.save(update_fields=['status', 'resolved_by', 'resolved_at', 'resolution_note', 'updated_at'])


def _audit_resolution(dispute, actor, request, note, settlement, why):
    record_audit(
        action='ORDER_DISPUTE_RESOLVED',
        actor=actor,
        request=request,
        target_type='OrderDispute',
        target_id=str(dispute.id),
        target_repr=f'Dispute on {dispute.order.order_no}',
        metadata={
            'order_id': str(dispute.order_id),
            'outcome': dispute.status,
            'settlement_status': getattr(settlement, 'status', None),
            'why': why or '',
            'note': note or '',
        },
    )


# -- Messaging --------------------------------------------------------------

def _notify_opened(dispute):
    from marketplace.models import Notification
    from marketplace.services.notification_service import NotificationService

    order = dispute.order
    NotificationService.notify_user(
        user=order.user,
        title="We've received your report",
        body=f"Thanks for letting us know about order {order.order_no}. "
             "The payment is on hold while our support team looks into it.",
        type=Notification.Type.ORDER,
        category='ORDER_DISPUTE',
        related_order=order,
        dedup_key=f'dispute_opened_customer:{dispute.id}',
    )
    NotificationService.notify_user(
        user=order.laundry.owner,
        title='A customer reported a problem',
        body=f"The customer reported a problem with order {order.order_no}. "
             "Payment for this order is on hold while Simame support reviews it.",
        type=Notification.Type.ORDER,
        category='ORDER_DISPUTE',
        related_order=order,
        dedup_key=f'dispute_opened_owner:{dispute.id}',
    )
    NotificationService.notify_admins(
        title='New order dispute',
        body=f"Order {order.order_no}: {dispute.get_reason_display()}. Settlement is on hold.",
        category='ORDER_DISPUTE',
        priority=Notification.Priority.HIGH,
        related_order=order,
        dedup_key=f'dispute_opened_admin:{dispute.id}',
    )


def _notify_resolved(dispute):
    from marketplace.models import Notification
    from marketplace.services.notification_service import NotificationService

    order = dispute.order
    if dispute.status == OrderDispute.Status.RESOLVED_REFUNDED:
        customer_body = f"We've resolved your report on order {order.order_no}. Your refund is on its way."
        owner_body = f"Support resolved the report on order {order.order_no} in the customer's favour. The order was refunded."
    elif dispute.status == OrderDispute.Status.RESOLVED_RELEASED:
        customer_body = f"We've reviewed your report on order {order.order_no} and closed it. Contact support if you need more help."
        owner_body = f"Support resolved the report on order {order.order_no}. Your earnings for it are released."
    else:
        customer_body = f"Our team is still reviewing order {order.order_no}. We'll be in touch."
        owner_body = f"The report on order {order.order_no} is with our finance team for review."

    for user, body, who in (
        (order.user, customer_body, 'customer'),
        (order.laundry.owner, owner_body, 'owner'),
    ):
        NotificationService.notify_user(
            user=user,
            title='Update on your reported order' if who == 'customer' else 'Order report resolved',
            body=body,
            type=Notification.Type.ORDER,
            category='ORDER_DISPUTE',
            related_order=order,
            dedup_key=f'dispute_resolved_{who}:{dispute.id}:{dispute.status}',
        )


def _alert_finance(dispute, settlement):
    from marketplace.models import Notification
    from marketplace.services.notification_service import NotificationService

    NotificationService.notify_admins(
        title='Dispute needs manual financial review',
        body=(
            f"Order {dispute.order.order_no}: settlement is "
            f"{getattr(settlement, 'status', 'missing')}. Nothing was refunded or reversed automatically."
        ),
        category='ORDER_DISPUTE',
        priority=Notification.Priority.HIGH,
        related_order=dispute.order,
        dedup_key=f'dispute_manual_review:{dispute.id}',
    )
    _notify_resolved(dispute)
