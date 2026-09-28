from django.core.management.base import BaseCommand

from admin_notifications.models import AdminNotificationDelivery
from admin_notifications.phone import mask_recipient
from admin_notifications.services.dispatcher import dispatch_due, requeue


class Command(BaseCommand):
    help = (
        'Send due admin order notifications from the outbox. Safe to run on a cron and in parallel '
        'with the web process (rows are claimed with SKIP LOCKED).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help='Max deliveries to claim this run.')
        parser.add_argument('--event-id', default=None, help='Only this event (UUID).')
        parser.add_argument('--channel', choices=['SMS', 'WHATSAPP', 'TELEGRAM', 'sms', 'whatsapp', 'telegram'], default=None)
        parser.add_argument('--dry-run', action='store_true', help='Render due messages; send nothing.')
        parser.add_argument('--retry-failed', action='store_true',
                            help='Re-queue FAILED/DEAD/UNKNOWN/UNDELIVERED deliveries first (after a fix).')

    def handle(self, *args, **options):
        if options['retry_failed'] and not options['dry_run']:
            qs = AdminNotificationDelivery.objects.all()
            if options['event_id']:
                qs = qs.filter(event_id=options['event_id'])
            if options['channel']:
                qs = qs.filter(channel=options['channel'].upper())
            self.stdout.write(f'Re-queued {requeue(qs)} delivery(ies).')

        result = dispatch_due(limit=options['limit'], event_id=options['event_id'],
                              channel=options['channel'], dry_run=options['dry_run'])
        if options['dry_run']:
            for delivery, parts in result.dry_run:
                self.stdout.write(self.style.MIGRATE_HEADING(
                    f'{delivery.event.event_type} {delivery.channel} -> {mask_recipient(delivery.recipient)} '
                    f'({len(parts)} part(s))'))
                for part in parts:
                    self.stdout.write(part if isinstance(part, str) else part.body)
                    self.stdout.write('-' * 40)
            self.stdout.write(f'Dry run: {len(result.dry_run)} delivery(ies) due; nothing sent.')
            return
        self.stdout.write(
            f'claimed={result.claimed} accepted={result.accepted} retry={result.retry} failed={result.failed} '
            f'unknown={result.unknown} dead={result.dead} expired={result.expired} recovered={result.recovered}'
        )
