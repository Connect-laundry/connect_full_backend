"""Delete idempotency records past their retention window.

    python manage.py purge_idempotency_records
    python manage.py purge_idempotency_records --hours 48 --dry-run

Records are only honoured for 24 hours (see
``config.middleware.idempotency.RETENTION``), so anything older is dead weight.
Safe to run on a schedule or by hand.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from marketplace.models import IdempotencyRecord


class Command(BaseCommand):
    help = "Delete idempotency records older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours', type=int, default=24,
            help='Delete records older than this many hours (default 24).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be deleted without deleting it.',
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options['hours'])
        stale = IdempotencyRecord.objects.filter(created_at__lt=cutoff)
        count = stale.count()

        if options['dry_run']:
            self.stdout.write(f"Would delete {count} record(s) older than {cutoff:%Y-%m-%d %H:%M}.")
            return

        stale.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} idempotency record(s)."))
