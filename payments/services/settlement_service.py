"""
Tracking what the platform owes each laundry.

Customers pay into the platform's Paystack account, so a successful payment
creates a debt to the laundry that does the work. That debt used to exist
nowhere: the money arrived, and who it belonged to was a matter of memory. With
the platform taking no commission, every cedi collected is owed onward, which
makes the record more important rather than less.

Amounts come from the order's frozen price snapshot, never from a live
recomputation, so a laundry changing its prices cannot alter a debt already
incurred.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import OrderSettlement, Payout

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')


def _money(value):
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(Decimal('0.01'))


class SettlementService:
    """Creates and reverses the platform's debts to laundries."""

    @staticmethod
    @transaction.atomic
    def record_for_order(order, processor_fee=None, settled_directly=False):
        """
        Record what this order's laundry is owed.

        Idempotent: webhooks retry, and Paystack may deliver `charge.success`
        more than once. Returns the existing settlement unchanged rather than
        double-crediting a laundry.

        ``settled_directly`` means Paystack already sent the money to the
        laundry's subaccount. The row is still written, because the platform
        needs a complete picture of what each laundry has earned, but it is
        marked paid so it never appears as an outstanding debt.
        """
        existing = OrderSettlement.objects.filter(order=order).first()
        if existing is not None:
            # A reversed settlement stays reversed; a repeat webhook must not
            # resurrect a debt that a refund cancelled.
            return existing

        gross = _money(getattr(order, 'total_amount', ZERO))
        commission = _money(getattr(order, 'platform_fee', ZERO))
        net = gross - commission
        if net < ZERO:
            # Commission cannot exceed what the customer paid. Clamping keeps a
            # bad configuration from writing a negative debt into the ledger.
            logger.error(
                "Settlement commission exceeded gross",
                extra={"order_id": str(order.id), "gross": str(gross), "commission": str(commission)},
            )
            net = ZERO

        settlement = OrderSettlement.objects.create(
            order=order,
            laundry=order.laundry,
            gross_amount=gross,
            platform_commission=commission,
            processor_fee=_money(processor_fee),
            net_payable=net,
            currency=getattr(order, 'currency', None) or 'GHS',
            route=(
                OrderSettlement.Route.DIRECT
                if settled_directly
                else OrderSettlement.Route.PLATFORM
            ),
            # Held, not payable. The customer has paid but the laundry has not
            # yet done the work, and paying at this point would mean a laundry
            # could be paid for clothes it never collected. Released by
            # `release_for_order` when the order reaches the customer.
            #
            # Direct settlement is the exception: Paystack already sent that
            # money, so there is nothing left to hold.
            status=(
                OrderSettlement.Status.PAID
                if settled_directly
                else OrderSettlement.Status.HELD
            ),
        )

        logger.info(
            "Settlement recorded",
            extra={
                "order_id": str(order.id),
                "laundry_id": str(order.laundry_id),
                "net_payable": str(net),
            },
        )
        return settlement

    @staticmethod
    @transaction.atomic
    def release_for_order(order, confirmed=True):
        """
        Move a held settlement towards payable, once the order was delivered.

        This is the escrow release. Until it runs, the customer's money sits
        with the platform and no payout can sweep it.

        ``confirmed`` says whether the delivery was proved with the customer's
        handover code. A proved delivery releases immediately. An unproved one
        only sets a timer: the laundry closed the order on its own word, so the
        customer gets a window to say otherwise before the money moves.

        Returns None when there is nothing held: already released, already
        paid, reversed, or settled directly to the laundry's own subaccount.
        """
        settlement = (
            OrderSettlement.objects.select_for_update().filter(order=order).first()
        )
        if settlement is None or settlement.status != OrderSettlement.Status.HELD:
            return None

        if not confirmed:
            hours = int(getattr(settings, 'SETTLEMENT_AUTO_RELEASE_HOURS', 48))
            settlement.release_after = timezone.now() + timedelta(hours=hours)
            settlement.save(update_fields=['release_after', 'updated_at'])
            logger.info(
                "Settlement release scheduled after dispute window",
                extra={
                    "order_id": str(order.id),
                    "release_after": settlement.release_after.isoformat(),
                },
            )
            return settlement

        settlement.status = OrderSettlement.Status.PENDING
        settlement.release_after = None
        settlement.save(update_fields=['status', 'release_after', 'updated_at'])

        logger.info(
            "Settlement released for payout",
            extra={
                "order_id": str(order.id),
                "laundry_id": str(settlement.laundry_id),
                "net_payable": str(settlement.net_payable),
            },
        )
        return settlement

    @staticmethod
    @transaction.atomic
    def reverse_for_order(order, reason=''):
        """
        Cancel the debt for a refunded order.

        Returns None when there is nothing to reverse. A settlement already
        paid out is still marked reversed, because the platform has genuinely
        overpaid and needs that visible rather than silently absorbed.
        """
        settlement = (
            OrderSettlement.objects.select_for_update().filter(order=order).first()
        )
        if settlement is None:
            return None
        if settlement.status == OrderSettlement.Status.REVERSED:
            return settlement

        was_paid = settlement.status == OrderSettlement.Status.PAID
        settlement.status = OrderSettlement.Status.REVERSED
        settlement.reversed_at = timezone.now()
        settlement.reversal_reason = reason or 'Order refunded'
        settlement.save(update_fields=['status', 'reversed_at', 'reversal_reason', 'updated_at'])

        if was_paid:
            logger.warning(
                "Settlement reversed after payout: the laundry has been overpaid",
                extra={
                    "order_id": str(order.id),
                    "laundry_id": str(settlement.laundry_id),
                    "amount": str(settlement.net_payable),
                },
            )

        return settlement

    @staticmethod
    def run_auto_release(now=None):
        """
        Release every settlement whose dispute window has passed.

        Unproved deliveries park on a timer rather than releasing outright.
        This is what eventually pays them, so a laundry that could not reach a
        customer for the handover code is not left unpaid forever.

        Returns the number released.
        """
        now = now or timezone.now()
        due = OrderSettlement.objects.filter(
            status=OrderSettlement.Status.HELD,
            release_after__isnull=False,
            release_after__lte=now,
        )

        released = 0
        for settlement in due.select_for_update(skip_locked=True).iterator():
            with transaction.atomic():
                settlement.status = OrderSettlement.Status.PENDING
                settlement.release_after = None
                settlement.save(update_fields=['status', 'release_after', 'updated_at'])
                released += 1

        if released:
            logger.info("Auto-released settlements", extra={"count": released})
        return released

    @staticmethod
    def run_scheduled_payouts(minimum=None):
        """
        Build a payout for every laundry with money waiting.

        Runs on a schedule so nobody has to remember to press a button. Uber
        Eats and DoorDash both settle merchants on a fixed cycle rather than
        per order, for the same reason: a predictable date a vendor can plan
        around beats an unpredictable favour.

        Payouts are created as DRAFT. Money does not move here — this decides
        who is owed what, and the transfer stays a separate, deliberate step.

        Returns the payouts created.
        """
        from laundries.models.laundry import Laundry

        floor = Decimal(str(minimum if minimum is not None else getattr(
            settings, 'PAYOUT_MINIMUM_AMOUNT', '1.00'
        )))

        laundry_ids = (
            OrderSettlement.objects.filter(status=OrderSettlement.Status.PENDING)
            .values_list('laundry_id', flat=True)
            .distinct()
        )

        created = []
        for laundry in Laundry.objects.filter(id__in=list(laundry_ids)):
            if SettlementService.outstanding_total(laundry) < floor:
                continue
            payout = SettlementService.build_payout(
                laundry, notes='Created by the scheduled payout run.'
            )
            if payout is not None:
                created.append(payout)

        logger.info(
            "Scheduled payout run complete",
            extra={"payouts_created": len(created)},
        )
        return created

    @staticmethod
    def outstanding_total(laundry):
        """
        Everything a laundry has earned and can be paid.

        Deliberately excludes HELD: money from orders still in progress has
        been paid by the customer but not yet earned, and showing it as
        outstanding would promise a laundry money it might not keep.
        """
        total = OrderSettlement.objects.filter(
            laundry=laundry, status=OrderSettlement.Status.PENDING
        ).aggregate(total=Sum('net_payable'))['total']
        return _money(total)

    @staticmethod
    def held_total(laundry):
        """Money received for this laundry's orders that are not yet delivered."""
        total = OrderSettlement.objects.filter(
            laundry=laundry, status=OrderSettlement.Status.HELD
        ).aggregate(total=Sum('net_payable'))['total']
        return _money(total)

    @staticmethod
    def paid_total(laundry):
        """Everything already paid out to this laundry."""
        total = OrderSettlement.objects.filter(
            laundry=laundry, status=OrderSettlement.Status.PAID
        ).aggregate(total=Sum('net_payable'))['total']
        return _money(total)

    @staticmethod
    def earnings_summary(laundry):
        """The three numbers a laundry owner needs: held, available, paid."""
        return {
            'held': str(SettlementService.held_total(laundry)),
            'available': str(SettlementService.outstanding_total(laundry)),
            'paid': str(SettlementService.paid_total(laundry)),
            'currency': 'GHS',
        }

    @staticmethod
    @transaction.atomic
    def build_payout(laundry, method=Payout.Method.MANUAL, reference='', notes=''):
        """
        Gather every pending settlement for a laundry into one payout.

        The settlements are locked and moved to SCHEDULED in the same
        transaction, so two operators building a payout at once cannot both
        claim the same debts.
        """
        pending = list(
            OrderSettlement.objects.select_for_update()
            .filter(laundry=laundry, status=OrderSettlement.Status.PENDING)
            .order_by('created_at')
        )
        if not pending:
            return None

        amount = sum((s.net_payable for s in pending), ZERO)
        payout = Payout.objects.create(
            laundry=laundry,
            amount=_money(amount),
            currency=pending[0].currency,
            method=method,
            reference=reference,
            notes=notes,
            period_start=pending[0].created_at,
            period_end=pending[-1].created_at,
            status=Payout.Status.DRAFT,
        )

        OrderSettlement.objects.filter(id__in=[s.id for s in pending]).update(
            payout=payout,
            status=OrderSettlement.Status.SCHEDULED,
            updated_at=timezone.now(),
        )
        return payout

    @staticmethod
    @transaction.atomic
    def mark_payout_paid(payout, reference=''):
        """Settle a payout and everything attached to it."""
        payout.status = Payout.Status.PAID
        payout.paid_at = timezone.now()
        if reference:
            payout.reference = reference
        payout.save(update_fields=['status', 'paid_at', 'reference', 'updated_at'])

        # Reversed settlements stay reversed: a refunded order is not paid out
        # just because it happened to be swept into this batch.
        payout.settlements.filter(status=OrderSettlement.Status.SCHEDULED).update(
            status=OrderSettlement.Status.PAID,
            updated_at=timezone.now(),
        )
        return payout
