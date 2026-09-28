"""NotificationDispatcher: sends due outbox deliveries to the providers.

Safe to run from many processes at once (web workers' post-commit kick, the
in-process sweep, `manage.py dispatch_admin_notifications` on a cron):

1. Claim: a short transaction selects due rows with
   ``SELECT ... FOR UPDATE SKIP LOCKED``, marks them SENDING with a fresh
   claim token and commits. Two dispatchers never claim the same row.
2. Send: provider HTTP calls run with no transaction and no row lock held.
3. Finish: every write back is conditional on the claim token, so a worker
   whose claim was recovered cannot overwrite a newer outcome.

Retry policy
- RETRYABLE (network/DNS before sending, provider 5xx, 429): bounded
  exponential backoff (ADMIN_NOTIFICATION_RETRY_SCHEDULE_SECONDS), then DEAD
  after ADMIN_NOTIFICATION_MAX_ATTEMPTS.
- FAILED (bad key, no balance, inactive gateway, bad sender/template/recipient,
  missing config): not retried until an operator fixes it and retries from
  Django admin or with ``--retry-failed``.
- UNKNOWN (read timeout, connection lost after sending, unreadable 2xx, worker
  died mid-send): not retried automatically, because the provider may have
  delivered it and three admins plus an SMS already cover the alert. Surfaced
  in Sentry and Django admin for a manual decision.
- Rows still unsent after ADMIN_NOTIFICATION_MAX_AGE_HOURS become DEAD
  (EXPIRED): a stale booking alert is not re-sent a day late.
- A later event for the same order and recipient waits while an earlier one
  is still in flight, so "CANCELLED" never arrives before "NEW ORDER".
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from .. import conf
from ..models import AdminNotificationDelivery as Delivery
from ..models import AdminNotificationEvent as Event
from ..models import AdminNotificationProviderMessage as ProviderMessage
from ..providers import base, get_sms_provider, get_telegram_provider, get_whatsapp_provider
from ..rendering import render_sms_parts, render_telegram_parts, render_whatsapp_parts, sms_segments
from . import metrics

logger = logging.getLogger('admin_notifications')

S = Delivery.Status


@dataclass
class DispatchResult:
    claimed: int = 0
    accepted: int = 0
    retry: int = 0
    failed: int = 0
    unknown: int = 0
    dead: int = 0
    expired: int = 0
    recovered: int = 0
    dry_run: list = field(default_factory=list)


def _channels(channel: str | None) -> list[str]:
    enabled = []
    if conf.whatsapp_enabled():
        enabled.append(Delivery.Channel.WHATSAPP)
    if conf.sms_enabled():
        enabled.append(Delivery.Channel.SMS)
    if conf.telegram_enabled():
        enabled.append(Delivery.Channel.TELEGRAM)
    if channel:
        enabled = [c for c in enabled if c == channel.upper()]
    return enabled


def expire_and_recover(now=None) -> tuple[int, int]:
    """DEAD for too-old unsent rows; UNKNOWN for claims abandoned mid-send."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=conf.max_age_hours())
    expired = Delivery.objects.filter(status__in=[S.PENDING, S.RETRY], created_at__lt=cutoff).update(
        status=S.DEAD, failed_at=now, last_error_class=base.EXPIRED,
        last_error_safe_message=f'Not sent within {conf.max_age_hours()}h; expired.', claim_token=None,
    )
    lease_cutoff = now - timedelta(seconds=conf.claim_lease_seconds())
    stuck = list(Delivery.objects.filter(status=S.SENDING, claimed_at__lt=lease_cutoff).values_list('id', flat=True))
    recovered = 0
    if stuck:
        recovered = Delivery.objects.filter(id__in=stuck, status=S.SENDING, claimed_at__lt=lease_cutoff).update(
            status=S.UNKNOWN, failed_at=now, last_error_class=base.WORKER_INTERRUPTED,
            last_error_safe_message='Worker stopped mid-send; the message may or may not have gone out.',
            claim_token=None,
        )
        for delivery in Delivery.objects.filter(id__in=stuck).select_related('event'):
            metrics.alert(metrics.UNKNOWN, 'Admin notification interrupted mid-send (possible duplicate risk)',
                          level='warning', channel=delivery.channel, provider=delivery.provider,
                          event_type=delivery.event.event_type, error_class=base.WORKER_INTERRUPTED)
    if expired:
        metrics.alert(metrics.DEAD, f'{expired} admin notification(s) expired unsent', level='error',
                      error_class=base.EXPIRED)
    return expired, recovered


def _claim(limit: int, channels: list[str], event_id=None, now=None) -> tuple[list, uuid.UUID]:
    now = now or timezone.now()
    token = uuid.uuid4()
    earlier_in_flight = Delivery.objects.filter(
        event__order_id=OuterRef('event__order_id'),
        channel=OuterRef('channel'),
        recipient=OuterRef('recipient'),
        event__created_at__lt=OuterRef('event__created_at'),
        status__in=[S.PENDING, S.RETRY, S.SENDING],
    )
    with transaction.atomic():
        qs = Delivery.objects.filter(
            status__in=[S.PENDING, S.RETRY], next_attempt_at__lte=now, channel__in=channels,
        ).filter(~Exists(earlier_in_flight))
        if event_id:
            qs = qs.filter(event_id=event_id)
        if connection.features.has_select_for_update_skip_locked:
            qs = qs.select_for_update(skip_locked=True, of=('self',))
        ids = list(qs.order_by('next_attempt_at', 'created_at').values_list('id', flat=True)[:limit])
        if ids:
            Delivery.objects.filter(id__in=ids).update(
                status=S.SENDING, claimed_at=now, claim_token=token, attempt_count=F('attempt_count') + 1,
            )
    return ids, token


def _mine(delivery_id, token):
    return Delivery.objects.filter(id=delivery_id, claim_token=token, status=S.SENDING)


def _finish_event(event_id):
    active = Delivery.objects.filter(event_id=event_id, status__in=Delivery.ACTIVE_STATUSES).exists()
    if not active:
        Event.objects.filter(pk=event_id, processed_at__isnull=True).update(processed_at=timezone.now())


def _render(delivery):
    payload = delivery.event.payload
    if delivery.channel == Delivery.Channel.SMS:
        return render_sms_parts(payload)
    if delivery.channel == Delivery.Channel.TELEGRAM:
        return render_telegram_parts(payload)
    return render_whatsapp_parts(payload)


def _send_part(delivery, part):
    if delivery.channel == Delivery.Channel.SMS:
        return get_sms_provider().send(delivery.recipient, part)
    if delivery.channel == Delivery.Channel.TELEGRAM:
        return get_telegram_provider().send(delivery.recipient, part)
    return get_whatsapp_provider().send(delivery.recipient, part.template_key, part.params)


def _apply_failure(delivery, token, result: base.SendResult, now):
    tags = dict(channel=delivery.channel, provider=delivery.provider, event_type=delivery.event.event_type,
                error_class=result.error_class, order_no=delivery.event.payload.get('order', {}).get('order_no', ''))
    common = dict(claim_token=None, last_error_class=result.error_class,
                  last_error_safe_message=result.safe_message[:300])
    if result.outcome == base.RETRYABLE:
        if delivery.attempt_count >= conf.max_attempts():
            _mine(delivery.id, token).update(status=S.DEAD, failed_at=now, **common)
            metrics.alert(metrics.DEAD, 'Admin notification retries exhausted', level='error', **tags)
            return 'dead'
        schedule = conf.retry_schedule_seconds()
        delay = schedule[min(delivery.attempt_count - 1, len(schedule) - 1)]
        _mine(delivery.id, token).update(status=S.RETRY, next_attempt_at=now + timedelta(seconds=delay), **common)
        metrics.record(metrics.RETRY, level=logging.WARNING, attempt=delivery.attempt_count, delay_seconds=delay,
                       **tags)
        return 'retry'
    if result.outcome == base.UNKNOWN:
        _mine(delivery.id, token).update(status=S.UNKNOWN, failed_at=now, **common)
        metrics.alert(metrics.UNKNOWN, 'Admin notification outcome unknown (possible duplicate risk)',
                      level='warning', **tags)
        return 'unknown'
    _mine(delivery.id, token).update(status=S.FAILED, failed_at=now, **common)
    level = 'error'
    title = 'Admin notification failed'
    if result.error_class == base.INSUFFICIENT_BALANCE:
        title = 'ARKESEL BALANCE EXHAUSTED: admin order alerts are not being sent on this channel'
        level = 'fatal'
    elif result.error_class in base.CHANNEL_BLOCKING:
        title = f'Admin notification channel blocked: {result.error_class}'
    metrics.alert(metrics.FAILED, title, level=level, **tags)
    return 'failed'


def _record_provider_message(delivery, index, part, message_id):
    """Remember an accepted part. The send already succeeded, so a bookkeeping
    conflict (a provider reusing an id) must not turn it into a failure."""
    defaults = {
        'provider': delivery.provider,
        'provider_message_id': message_id or None,
        'provider_status': 'SUBMITTED' if delivery.channel == Delivery.Channel.SMS else 'SENT',
        'estimated_segments': sms_segments(part) if delivery.channel == Delivery.Channel.SMS else 0,
    }
    try:
        with transaction.atomic():
            ProviderMessage.objects.get_or_create(delivery_id=delivery.id, part_index=index, defaults=defaults)
    except IntegrityError:
        logger.warning('Provider returned a message id already recorded; stored without it',
                       extra={'event': 'admin_notification.duplicate_provider_id', 'provider': delivery.provider})
        defaults['provider_message_id'] = None
        ProviderMessage.objects.get_or_create(delivery_id=delivery.id, part_index=index, defaults=defaults)


def _process(delivery_id, token) -> str:
    delivery = Delivery.objects.select_related('event').get(pk=delivery_id)
    now = timezone.now()
    try:
        parts = _render(delivery)
    except Exception as exc:
        # Traceback to the server log only: Sentry would attach frame locals,
        # which here hold customer names and addresses.
        logger.warning('Admin notification render failed', exc_info=True,
                       extra={'event': 'admin_notification.render_error'})
        return _apply_failure(delivery, token, base.SendResult(
            base.FAILED, error_class=base.RENDER_ERROR, safe_message=type(exc).__name__), now)

    total = len(parts)
    segments = sum(sms_segments(p) for p in parts) if delivery.channel == Delivery.Channel.SMS else 0
    _mine(delivery.id, token).update(parts_total=total, estimated_segments=segments)

    first_id = delivery.provider_message_id
    for index in range(delivery.parts_sent, total):
        part = parts[index]
        result = _send_part(delivery, part)
        now = timezone.now()
        if not result.accepted:
            return _apply_failure(delivery, token, result, now)
        _record_provider_message(delivery, index, part, result.provider_message_id)
        first_id = first_id or result.provider_message_id
        if not _mine(delivery.id, token).update(parts_sent=index + 1, provider_message_id=first_id or ''):
            return 'lost_claim'

    final = S.SUBMITTED if delivery.channel == Delivery.Channel.SMS else S.SENT
    _mine(delivery.id, token).update(
        status=final, submitted_at=timezone.now(), claim_token=None,
        last_error_class='', last_error_safe_message='',
    )
    metrics.record(metrics.SUBMITTED, channel=delivery.channel, provider=delivery.provider,
                   event_type=delivery.event.event_type,
                   order_no=delivery.event.payload.get('order', {}).get('order_no', ''),
                   parts=total, segments=segments,
                   latency_seconds=round((timezone.now() - delivery.event.created_at).total_seconds(), 1))
    return 'accepted'


def dispatch_due(*, limit=None, event_id=None, channel=None, dry_run=False) -> DispatchResult:
    """Send what is due now. Never raises for a single bad delivery."""
    result = DispatchResult()
    if dry_run:
        qs = Delivery.objects.select_related('event').filter(status__in=[S.PENDING, S.RETRY])
        if event_id:
            qs = qs.filter(event_id=event_id)
        if channel:
            qs = qs.filter(channel=channel.upper())
        for delivery in qs.order_by('created_at')[: limit or conf.batch_size()]:
            parts = _render(delivery)
            result.dry_run.append((delivery, parts))
        return result

    result.expired, result.recovered = expire_and_recover()
    if not conf.notifications_enabled():
        return result
    channels = _channels(channel)
    if not channels:
        return result
    ids, token = _claim(limit or conf.batch_size(), channels, event_id=event_id)
    result.claimed = len(ids)
    for delivery_id in ids:
        try:
            outcome = _process(delivery_id, token)
        except Exception as exc:
            logger.warning('Admin notification dispatch error', exc_info=True,
                           extra={'event': 'admin_notification.dispatch_error'})
            delivery = Delivery.objects.select_related('event').get(pk=delivery_id)
            outcome = _apply_failure(delivery, token, base.SendResult(
                base.UNKNOWN, error_class=type(exc).__name__[:40],
                safe_message='Unexpected dispatcher error; check logs.'), timezone.now())
        if outcome == 'accepted':
            result.accepted += 1
        elif outcome in ('retry', 'dead', 'failed', 'unknown'):
            setattr(result, outcome, getattr(result, outcome) + 1)
        event_id_of = Delivery.objects.filter(pk=delivery_id).values_list('event_id', flat=True).first()
        if event_id_of:
            _finish_event(event_id_of)
    return result


def requeue(queryset, *, reset_attempts=True) -> int:
    """Operator retry for FAILED/DEAD/UNKNOWN/UNDELIVERED deliveries.

    Parts the provider already accepted are not re-sent. An UNDELIVERED SMS
    resends every part, since none reached the handset.
    """
    now = timezone.now()
    count = 0
    for delivery in queryset.filter(status__in=Delivery.RETRYABLE_BY_ADMIN):
        updates = dict(status=S.PENDING, next_attempt_at=now, claim_token=None, failed_at=None,
                       last_error_class='', last_error_safe_message='')
        if reset_attempts:
            updates['attempt_count'] = 0
        if delivery.status == S.UNDELIVERED:
            updates['parts_sent'] = 0
            ProviderMessage.objects.filter(delivery=delivery).delete()
        count += Delivery.objects.filter(pk=delivery.pk, status=delivery.status).update(**updates)
        Event.objects.filter(pk=delivery.event_id).update(processed_at=None)
    return count
