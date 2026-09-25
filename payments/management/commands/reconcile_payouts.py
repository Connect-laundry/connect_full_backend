"""
Reconcile internal PROCESSING and WAITING_FOR_FUNDS payouts against Paystack.

This command safely compares internal payout state against Paystack's Transfer API.
Used when webhooks are delayed, missed, or network partitions occur.

Safety Guarantees:
- NEVER blindly resends unknown or in-flight transfers.
- Atomically transitions status to PAID, FAILED, or REVERSED only when confirmed by Paystack.
- Leaves indeterminate outcomes in PROCESSING/REVIEW for manual review.
- Idempotent and safe to run on cron or manually.
"""

import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from payments.models import Payout
from payments.services.paystack import PaystackService
from payments.services.payout_service import PayoutService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconcile internal PROCESSING / WAITING_FOR_FUNDS payouts against Paystack transfer status."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reference',
            type=str,
            default=None,
            help='Reconcile a specific payout by reference.',
        )
        parser.add_argument(
            '--payout-id',
            type=str,
            default=None,
            help='Reconcile a specific payout by UUID.',
        )
        parser.add_argument(
            '--retry-waiting',
            action='store_true',
            help='Attempt to re-send payouts currently in WAITING_FOR_FUNDS.',
        )

    def handle(self, *args, **options):
        reference = options.get('reference')
        payout_id = options.get('payout_id')
        retry_waiting = options.get('retry_waiting', False)

        paystack = PaystackService()

        qs = Payout.objects.all()
        if reference:
            qs = qs.filter(reference=reference)
        elif payout_id:
            qs = qs.filter(id=payout_id)
        else:
            statuses = [Payout.Status.PROCESSING]
            if retry_waiting:
                statuses.append(Payout.Status.WAITING_FOR_FUNDS)
            qs = qs.filter(status__in=statuses)

        payouts = list(qs.order_by('created_at'))
        if not payouts:
            self.stdout.write(self.style.SUCCESS("No payouts require reconciliation."))
            return

        self.stdout.write(f"Reconciling {len(payouts)} payout(s)...")

        settled_count = 0
        failed_count = 0
        reversed_count = 0
        unchanged_count = 0

        for payout in payouts:
            self.stdout.write(f"Checking Payout {payout.id} (ref: {payout.reference}, status: {payout.status})...")

            # Handle WAITING_FOR_FUNDS re-try if requested
            if payout.status == Payout.Status.WAITING_FOR_FUNDS:
                if retry_waiting:
                    self.stdout.write(f"  Attempting to re-send WAITING_FOR_FUNDS payout {payout.reference}...")
                    try:
                        PayoutService.send(payout, paystack=paystack)
                        self.stdout.write(self.style.SUCCESS(f"  Re-sent: payout now {payout.status}"))
                    except Exception as e:
                        self.stderr.write(self.style.WARNING(f"  Failed to re-send: {e}"))
                else:
                    self.stdout.write("  Skipping WAITING_FOR_FUNDS (use --retry-waiting to re-attempt).")
                    unchanged_count += 1
                continue

            # Query Paystack for status
            transfer_data = None
            if payout.paystack_transfer_code:
                resp = paystack.fetch_transfer(payout.paystack_transfer_code)
                if resp.get('status') and resp.get('data'):
                    transfer_data = resp['data']

            if not transfer_data and payout.reference:
                resp = paystack.verify_transfer(payout.reference)
                if resp.get('status') and resp.get('data'):
                    transfer_data = resp['data']

            if not transfer_data:
                self.stdout.write(
                    self.style.WARNING(f"  Could not determine Paystack transfer status for {payout.reference}. Kept in {payout.status}.")
                )
                unchanged_count += 1
                continue

            paystack_status = str(transfer_data.get('status', '')).lower()
            transfer_code = transfer_data.get('transfer_code') or payout.paystack_transfer_code
            if transfer_code and not payout.paystack_transfer_code:
                payout.paystack_transfer_code = transfer_code
                payout.save(update_fields=['paystack_transfer_code'])

            self.stdout.write(f"  Paystack status: {paystack_status}")

            if paystack_status == 'success':
                PayoutService.mark_transfer_settled(payout, reference=payout.reference)
                settled_count += 1
                self.stdout.write(self.style.SUCCESS(f"  -> Successfully settled (PAID)."))
            elif paystack_status in ['failed', 'rejected']:
                reason = transfer_data.get('reason') or 'Transfer marked failed during reconciliation'
                PayoutService.mark_transfer_failed(payout, reason=reason)
                failed_count += 1
                self.stdout.write(self.style.WARNING(f"  -> Marked FAILED (settlements restored to pending pool)."))
            elif paystack_status == 'reversed':
                reason = transfer_data.get('reason') or 'Transfer reversed according to provider'
                PayoutService.mark_transfer_reversed(payout, reason=reason)
                reversed_count += 1
                self.stdout.write(self.style.WARNING(f"  -> Marked REVERSED (settlements restored to pending pool)."))
            elif paystack_status in ['pending', 'processing', 'ongoing']:
                self.stdout.write(f"  -> Still in flight at Paystack ({paystack_status}). Unchanged.")
                unchanged_count += 1
            else:
                self.stdout.write(
                    self.style.WARNING(f"  -> Unknown provider status '{paystack_status}'. Kept in {payout.status} for manual review.")
                )
                unchanged_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nReconciliation complete: {settled_count} settled, {failed_count} failed, "
                f"{reversed_count} reversed, {unchanged_count} unchanged."
            )
        )
