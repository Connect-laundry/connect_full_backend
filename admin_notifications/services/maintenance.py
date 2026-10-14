"""Background upkeep for the notification outbox.

- ``reconcile_sms_reports``: SMS whose delivery callback never arrived are
  asked about through Arkesel's batch report endpoint. Results go through the
  same ``apply_sms_status`` as callbacks, so a reconciled status and a late
  callback can never disagree or downgrade each other.
- ``purge_old_payloads``: event payloads are frozen order snapshots holding
  customer names, phone numbers and addresses. Once an event is settled and
  older than the retention window, those fields are removed; the order
  reference and event type stay for audit and metrics.

Both run from the dispatcher's background pass at their own intervals and
from ``manage.py notification_maintenance``.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from ..models import AdminNotificationEvent as Event
from ..models import AdminNotificationProviderMessage as ProviderMessage
from ..models import MerchantSmsSendLog
from .callbacks import FINAL, apply_sms_status

RECONCILE_BATCH = 100
# Callbacks normally arrive within minutes. Ask only after this long, and stop
# asking once Arkesel no longer has an answer worth waiting for.
RECONCILE_AFTER = timedelta(minutes=15)
RECONCILE_UNTIL = timedelta(hours=48)
SEND_LOG_RETENTION = timedelta(days=90)


def retention_days() -> int:
    return max(1, int(getattr(settings, 'ADMIN_NOTIFICATION_PAYLOAD_RETENTION_DAYS', 30) or 30))


def reconcile_sms_reports(now=None, provider=None) -> dict:
    now = now or timezone.now()
    pending = list(
        ProviderMessage.objects.filter(
            provider='arkesel_sms', provider_message_id__isnull=False,
            created_at__lte=now - RECONCILE_AFTER, created_at__gte=now - RECONCILE_UNTIL,
        ).exclude(provider_status__in=FINAL)
        .order_by('created_at').values_list('provider_message_id', flat=True)[:RECONCILE_BATCH]
    )
    if not pending:
        return {'asked': 0, 'updated': 0}
    if provider is None:
        from ..providers import get_sms_provider
        provider = get_sms_provider()
    reports = provider.fetch_reports(pending)
    updated = sum(1 for sms_id, status in reports.items() if apply_sms_status(sms_id, status) == 'updated')
    return {'asked': len(pending), 'updated': updated}


SCRUBBED_KEYS = ('customer', 'pickup', 'delivery', 'laundry', 'items', 'notes', 'weight', 'money', 'payment',
                 'event', 'admin_url')


def purge_old_payloads(now=None) -> dict:
    now = now or timezone.now()
    cutoff = now - timedelta(days=retention_days())
    purged = 0
    candidates = Event.objects.filter(created_at__lt=cutoff, processed_at__isnull=False).exclude(
        payload__has_key='purged_at',
    ).only('id', 'payload')
    for event in candidates.iterator(chunk_size=200):
        payload = dict(event.payload or {})
        for key in SCRUBBED_KEYS:
            payload.pop(key, None)
        payload['purged_at'] = now.isoformat()
        Event.objects.filter(pk=event.pk).update(payload=payload)
        purged += 1
    logs = MerchantSmsSendLog.objects.filter(created_at__lt=now - SEND_LOG_RETENTION).delete()[0]
    return {'payloads_purged': purged, 'send_logs_deleted': logs}
