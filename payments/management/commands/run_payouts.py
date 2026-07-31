"""Release due settlements and build the payouts they belong to.

Runs on a schedule so nobody has to remember to press a button. Uber Eats and
DoorDash both settle merchants on a fixed weekly cycle rather than per order,
because a date a vendor can plan around beats an unpredictable favour.

Two ways to run it, and the command is the same either way:

* **Render Cron Job** (works today, needs no Redis)::

      python manage.py run_payouts

* **Celery beat**, if a worker is ever provisioned. See
  ``CELERY_BEAT_SCHEDULE`` in settings.

Money does not move here. This decides who is owed what and records it as a
DRAFT payout; sending the money stays a separate, deliberate step.
"""

from django.core.management.base import BaseCommand

from payments.services.settlement_service import SettlementService


class Command(BaseCommand):
    help = "Auto-release due settlements and build payouts for laundries with a balance."

    def add_arguments(self, parser):
        parser.add_argument(
            '--minimum', default=None,
            help='Skip laundries owed less than this (defaults to PAYOUT_MINIMUM_AMOUNT).',
        )
        parser.add_argument(
            '--release-only', action='store_true',
            help='Release settlements whose dispute window has passed, but build no payouts.',
        )
        parser.add_argument(
            '--send', action='store_true',
            help=(
                'Also transfer each payout via Paystack. Requires '
                'PAYSTACK_TRANSFERS_ENABLED. Without this the payouts are only drafted.'
            ),
        )

    def handle(self, *args, **options):
        released = SettlementService.run_auto_release()
        self.stdout.write(f"Released {released} settlement(s) past their dispute window.")

        if options['release_only']:
            return

        payouts = SettlementService.run_scheduled_payouts(minimum=options['minimum'])
        if not payouts:
            self.stdout.write("No laundry had a balance to pay out.")
            return

        total = sum(p.amount for p in payouts)
        self.stdout.write(
            self.style.SUCCESS(
                f"Built {len(payouts)} payout(s) totalling {payouts[0].currency} {total}."
            )
        )
        for payout in payouts:
            self.stdout.write(f"  {payout.laundry.name}: {payout.currency} {payout.amount}")

        if not options['send']:
            self.stdout.write("Drafted only. Re-run with --send to transfer them.")
            return

        # Each payout is sent independently: one laundry with a bad recipient
        # code must not stop everyone else from being paid.
        from payments.services.payout_service import PayoutError, PayoutService

        sent = 0
        for payout in payouts:
            try:
                PayoutService.send(payout)
                sent += 1
            except PayoutError as e:
                self.stderr.write(
                    self.style.WARNING(f"  {payout.laundry.name}: not sent — {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(f"Submitted {sent} of {len(payouts)} payout(s) to Paystack.")
        )
