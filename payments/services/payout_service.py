"""
Sending a payout to a laundry.

This is the one place in the system that moves money outward, so it is written
to be paranoid rather than clever. Every guard here exists to answer one
question: could this pay the wrong laundry, or pay the right one twice?

The guards, and why each is here:

* **A kill switch, off by default.** Nothing transfers until someone
  deliberately turns it on.
* **Only DRAFT payouts.** A payout already sent, sending, or failed is never
  re-sent by this code.
* **The amount is re-derived from the settlements**, not trusted from the
  payout row. If someone edits an amount, the transfer refuses rather than
  sending the edited figure.
* **A stable reference.** Paystack rejects duplicate references, so a retry
  after a network blip cannot pay twice.
* **Indeterminate outcomes are not failures.** A timeout may mean the money
  left. Those payouts are parked in PROCESSING for a human, never retried
  automatically.
* **A per-transfer ceiling.** A pricing bug that produced an absurd figure
  stops here instead of at someone's bank.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import OrderSettlement, Payout

logger = logging.getLogger(__name__)


class PayoutError(Exception):
    """A payout could not be sent. The payout is left untouched or PROCESSING."""


def transfers_enabled():
    """Master switch. Off until transfers are verified on a live account."""
    return bool(getattr(settings, 'PAYSTACK_TRANSFERS_ENABLED', False))


def _max_transfer():
    return Decimal(str(getattr(settings, 'PAYOUT_MAX_TRANSFER_AMOUNT', '5000.00')))


def transfer_reference(payout):
    """
    Stable, unique reference for a payout's transfer.

    Derived from the payout id so a retry sends the identical reference and
    Paystack rejects it as a duplicate rather than transferring again.
    """
    return f"PO-{str(payout.id).replace('-', '')[:24]}"


class PayoutService:

    @staticmethod
    def expected_amount(payout):
        """What this payout is worth according to its own settlements."""
        total = payout.settlements.filter(
            status=OrderSettlement.Status.SCHEDULED
        ).aggregate(total=Sum('net_payable'))['total']
        return Decimal(str(total or '0.00')).quantize(Decimal('0.01'))

    @staticmethod
    def check_sendable(payout):
        """
        Everything that must be true before money moves.

        Raises PayoutError with a reason rather than returning a bool, so the
        reason reaches the operator and the logs instead of being swallowed.
        """
        if not transfers_enabled():
            raise PayoutError("Automatic transfers are switched off.")

        if payout.status != Payout.Status.DRAFT:
            raise PayoutError(f"Payout is {payout.status}, not DRAFT.")

        recipient = (getattr(payout.laundry, 'paystack_recipient_code', '') or '').strip()
        if not recipient:
            raise PayoutError("Laundry has no Paystack recipient code.")

        amount = Decimal(str(payout.amount))
        if amount <= Decimal('0.00'):
            raise PayoutError("Payout amount is not positive.")

        if amount > _max_transfer():
            raise PayoutError(
                f"Payout of {amount} exceeds the {_max_transfer()} ceiling; send it manually."
            )

        expected = PayoutService.expected_amount(payout)
        if expected != amount:
            # The payout row and its settlements disagree. Something edited one
            # of them, and guessing which is right is not this code's job.
            raise PayoutError(
                f"Payout amount {amount} does not match its settlements ({expected})."
            )

        return recipient

    @staticmethod
    def send(payout, paystack=None):
        """
        Transfer a payout to its laundry.

        Returns the updated payout. On a clean rejection the payout goes back
        to DRAFT so it can be corrected and retried; on an unknown outcome it
        stays PROCESSING and is left alone.
        """
        recipient = PayoutService.check_sendable(payout)

        from .paystack import PaystackService
        client = paystack or PaystackService()
        reference = transfer_reference(payout)

        # Claim the payout before calling out, so a second run cannot pick up
        # the same one while this transfer is in flight.
        with transaction.atomic():
            claimed = Payout.objects.select_for_update().get(pk=payout.pk)
            if claimed.status != Payout.Status.DRAFT:
                raise PayoutError(f"Payout is {claimed.status}, not DRAFT.")
            claimed.status = Payout.Status.PROCESSING
            claimed.reference = reference
            claimed.method = Payout.Method.PAYSTACK
            claimed.save(update_fields=['status', 'reference', 'method', 'updated_at'])

        response = client.initiate_transfer(
            amount=claimed.amount,
            recipient_code=recipient,
            reference=reference,
            reason=f"Simame payout {reference}",
        )

        if response.get('status'):
            logger.info(
                "Payout transfer accepted",
                extra={"payout_id": str(claimed.id), "amount": str(claimed.amount)},
            )
            # Stays PROCESSING. Paystack confirms by webhook; treating an
            # accepted request as settled money would be a lie the ledger
            # cannot take back.
            return claimed

        if response.get('indeterminate'):
            logger.error(
                "Payout transfer outcome unknown; left for manual review",
                extra={"payout_id": str(claimed.id), "reference": mask(reference)},
            )
            raise PayoutError(
                "Transfer outcome unknown. The payout is left PROCESSING for manual review."
            )

        # A clean rejection: nothing was sent, so the payout can be corrected.
        message = response.get('message') or 'Transfer was rejected.'
        with transaction.atomic():
            claimed.status = Payout.Status.DRAFT
            claimed.failure_reason = str(message)[:500]
            claimed.save(update_fields=['status', 'failure_reason', 'updated_at'])

        logger.error(
            # Not `message`: that is a reserved LogRecord attribute and
            # passing it through `extra` raises rather than logging.
            "Payout transfer rejected",
            extra={"payout_id": str(claimed.id), "rejection_reason": str(message)},
        )
        raise PayoutError(message)

    @staticmethod
    @transaction.atomic
    def mark_transfer_settled(payout, reference=''):
        """Paystack confirmed the transfer landed."""
        from .settlement_service import SettlementService
        return SettlementService.mark_payout_paid(payout, reference=reference)

    @staticmethod
    @transaction.atomic
    def mark_transfer_failed(payout, reason=''):
        """
        Paystack could not deliver the transfer.

        The settlements go back to payable so the money is not lost from the
        ledger: the laundry is still owed it, and the next run will try again
        once whatever was wrong with the recipient is fixed.
        """
        locked = Payout.objects.select_for_update().get(pk=payout.pk)
        locked.status = Payout.Status.FAILED
        locked.failure_reason = str(reason or 'Transfer failed')[:500]
        locked.save(update_fields=['status', 'failure_reason', 'updated_at'])

        locked.settlements.filter(status=OrderSettlement.Status.SCHEDULED).update(
            payout=None,
            status=OrderSettlement.Status.PENDING,
            updated_at=timezone.now(),
        )
        logger.warning(
            "Payout failed; settlements returned to the payable pool",
            extra={"payout_id": str(locked.id), "reason": locked.failure_reason},
        )
        return locked


def mask(value):
    """Short helper so references are not written to logs in full."""
    from config.redaction import mask_reference
    return mask_reference(value)
