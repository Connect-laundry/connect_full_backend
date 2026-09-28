"""Arkesel SMS delivery reports.

Arkesel calls our callback_url with ``sms_id`` and ``status``. There is no
provider signature to verify, so trust comes from defence in depth:

1. the URL carries an unguessable secret (checked by the view);
2. the status must be one of Arkesel's documented values;
3. the sms_id must be one we recorded ourselves; anything else is ignored;
4. a final status is never downgraded by a late or replayed report, so
   duplicate callbacks are harmless.
"""
from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from ..models import AdminNotificationDelivery as Delivery
from ..models import AdminNotificationProviderMessage as ProviderMessage
from . import metrics

ARKESEL_STATUSES = {'DELIVERED', 'SUBMITTED', 'QUEUED', 'NOT_DELIVERED', 'PROHIBITED', 'EXPIRED'}
FINAL_FAILURE = {'NOT_DELIVERED', 'PROHIBITED', 'EXPIRED'}
FINAL = FINAL_FAILURE | {'DELIVERED'}

_SMS_ID = re.compile(r'[A-Za-z0-9._:-]{1,128}')

IGNORED = 'ignored'
UPDATED = 'updated'
DUPLICATE = 'duplicate'


def apply_sms_status(sms_id, status) -> str:
    sms_id = str(sms_id or '').strip()
    status = str(status or '').strip().upper()
    # Arkesel ids are UUIDs. Anything else (NUL bytes, which PostgreSQL
    # rejects with a DataError, oversized or odd input) is ignored before it
    # reaches the database.
    if not _SMS_ID.fullmatch(sms_id) or status not in ARKESEL_STATUSES:
        return IGNORED
    with transaction.atomic():
        message = (
            ProviderMessage.objects.select_for_update()
            .filter(provider='arkesel_sms', provider_message_id=sms_id)
            .first()
        )
        if message is None:
            return IGNORED
        if message.provider_status == status or message.provider_status in FINAL:
            return DUPLICATE
        now = timezone.now()
        message.provider_status = status
        message.status_updated_at = now
        message.save(update_fields=['provider_status', 'status_updated_at'])

        delivery = Delivery.objects.select_for_update().select_related('event').get(pk=message.delivery_id)
        if status not in FINAL or delivery.status not in (Delivery.Status.SUBMITTED, Delivery.Status.DELIVERED):
            return UPDATED
        parts = list(
            ProviderMessage.objects.filter(delivery_id=delivery.pk).values_list('provider_status', flat=True)
        )
        tags = dict(channel=delivery.channel, provider=delivery.provider, event_type=delivery.event.event_type,
                    order_no=delivery.event.payload.get('order', {}).get('order_no', ''))
        if status in FINAL_FAILURE:
            Delivery.objects.filter(pk=delivery.pk).update(
                status=Delivery.Status.UNDELIVERED, failed_at=now, last_error_class=f'SMS_{status}',
                last_error_safe_message=f'Arkesel delivery report: {status}.',
            )
            metrics.alert(metrics.UNDELIVERED, f'Admin SMS not delivered ({status})', level='warning',
                          error_class=f'SMS_{status}', **tags)
        elif len(parts) >= delivery.parts_total and all(s == 'DELIVERED' for s in parts):
            Delivery.objects.filter(pk=delivery.pk).update(status=Delivery.Status.DELIVERED, delivered_at=now)
            metrics.record(metrics.DELIVERED, **tags)
    return UPDATED
