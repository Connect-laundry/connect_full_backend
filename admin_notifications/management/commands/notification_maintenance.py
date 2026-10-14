"""Run notification upkeep now: SMS report reconciliation and payload purge."""
from django.core.management.base import BaseCommand

from admin_notifications.services import maintenance


class Command(BaseCommand):
    help = 'Reconcile SMS delivery reports with Arkesel and purge customer data from old alert payloads.'

    def add_arguments(self, parser):
        parser.add_argument('--skip-reconcile', action='store_true')
        parser.add_argument('--skip-purge', action='store_true')

    def handle(self, *args, **options):
        if not options['skip_reconcile']:
            self.stdout.write(f'reconcile: {maintenance.reconcile_sms_reports()}')
        if not options['skip_purge']:
            self.stdout.write(f'purge: {maintenance.purge_old_payloads()}')
