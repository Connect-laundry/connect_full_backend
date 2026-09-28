from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.utils import timezone

from admin_notifications.models import AdminNotificationDelivery as Delivery
from admin_notifications.models import AdminNotificationEvent as Event


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


class Command(BaseCommand):
    help = 'Operations report: orders notified, COD vs online, delivery success, latency, failures.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options['days'])
        events = Event.objects.filter(created_at__gte=since)
        new_orders = events.filter(event_type=Event.EventType.NEW_ORDER)
        cod = sum(1 for p in new_orders.values_list('payload', flat=True)[:10000]
                  if (p.get('payment') or {}).get('is_cod'))
        total_orders = new_orders.count()
        self.stdout.write(f'Window: last {options["days"]} day(s)')
        self.stdout.write(f'Orders notified (NEW_ORDER): {total_orders}  COD: {cod}  Online: {total_orders - cod}')
        for row in events.values('event_type').annotate(n=Count('id')).order_by('event_type'):
            self.stdout.write(f'  {row["event_type"]}: {row["n"]}')

        deliveries = Delivery.objects.filter(created_at__gte=since)
        for channel in (Delivery.Channel.WHATSAPP, Delivery.Channel.SMS, Delivery.Channel.TELEGRAM):
            qs = deliveries.filter(channel=channel)
            by_status = dict(qs.values_list('status').annotate(n=Count('id')))
            finished = sum(n for s, n in by_status.items() if s not in Delivery.ACTIVE_STATUSES)
            ok = sum(by_status.get(s, 0) for s in Delivery.ACCEPTED_STATUSES)
            rate = f'{100 * ok / finished:.1f}%' if finished else 'n/a'
            retries = (qs.aggregate(a=Sum('attempt_count'))['a'] or 0) - qs.exclude(attempt_count=0).count()
            latencies = [
                (submitted - created).total_seconds()
                for submitted, created in qs.filter(submitted_at__isnull=False)
                .values_list('submitted_at', 'event__created_at')[:10000]
            ]
            p50, p90 = _percentile(latencies, 50), _percentile(latencies, 90)
            self.stdout.write(f'{channel}: {sum(by_status.values())} deliveries, success {rate}, retries {retries}, '
                              f'dead {by_status.get("DEAD", 0)}, failed {by_status.get("FAILED", 0)}, '
                              f'unknown {by_status.get("UNKNOWN", 0)}')
            self.stdout.write(f'  status: {by_status}')
            self.stdout.write(
                '  dispatch latency p50: ' + (f'{p50:.1f}s' if p50 is not None else 'n/a')
                + '  p90: ' + (f'{p90:.1f}s' if p90 is not None else 'n/a'))
            if channel == Delivery.Channel.SMS:
                self.stdout.write(f'  SMS submitted {by_status.get("SUBMITTED", 0)} vs delivered '
                                  f'{by_status.get("DELIVERED", 0)}; estimated segments '
                                  f'{qs.aggregate(s=Sum("estimated_segments"))["s"] or 0}')
        failures = (deliveries.exclude(last_error_class='').values('channel', 'last_error_class')
                    .annotate(n=Count('id')).order_by('-n')[:10])
        for row in failures:
            self.stdout.write(f'  failure {row["channel"]} {row["last_error_class"]}: {row["n"]}')
