"""Retention: delete stored price-list photos older than the retention window.

    python manage.py purge_price_import_images [--days N] [--dry-run]

Defaults to PRICE_LIST_IMAGE_RETENTION_DAYS (30). Only the sanitised image
file is removed; the structured draft/confirm audit trail stays. Safe to run
daily from a cron/Render job; needs no worker.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from laundries.models.price_import import PriceListImportJob


class Command(BaseCommand):
    help = 'Delete price-list import images past the retention window.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        days = opts['days'] if opts['days'] is not None else settings.PRICE_LIST_IMAGE_RETENTION_DAYS
        cutoff = timezone.now() - timedelta(days=days)
        jobs = PriceListImportJob.objects.filter(created_at__lt=cutoff).exclude(source_image='')
        removed = failed = 0
        for job in jobs.iterator():
            if opts['dry_run']:
                removed += 1
                continue
            try:
                job.source_image.delete(save=False)
            except Exception:
                failed += 1
                continue
            PriceListImportJob.objects.filter(pk=job.pk).update(source_image='')
            removed += 1
        verb = 'would remove' if opts['dry_run'] else 'removed'
        self.stdout.write(f'{verb} {removed} image(s) older than {days} days; {failed} failed')
