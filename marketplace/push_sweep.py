"""In-process recovery for the push outbox when no Celery beat is running.

Direct-delivery mode (PUSH_USE_CELERY=false) has no minute-level sweep, so a
row left PENDING (process restart mid-send, Expo outage exhausting retries)
stayed PENDING forever: production had two such rows on 2026-09-22.

After a request finishes, each worker checks at most once per
PUSH_INPROCESS_SWEEP_SECONDS, on a background thread:

- PENDING rows older than PUSH_PENDING_MAX_AGE_HOURS are marked FAILED. An
  "Order placed" alert hours late is worse than none.
- Newer stale rows go back through claim_push -> dispatch, so the post-commit
  sender, other workers and a later sweep can never send the same push twice.
- Accepted tickets are resolved into APNs/FCM receipts (DELIVERED/FAILED),
  dead tokens are deactivated and credential failures alert admins.
"""
import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_next_run_at = 0.0


def expire_stale_pending_pushes(now=None):
    from marketplace.models import Notification

    now = now or timezone.now()
    max_age = timedelta(hours=getattr(settings, 'PUSH_PENDING_MAX_AGE_HOURS', 6))
    expired = Notification.objects.filter(
        push_status=Notification.PushStatus.PENDING,
        created_at__lt=now - max_age,
    ).update(push_status=Notification.PushStatus.FAILED, delivered_at=None)
    if expired:
        logger.warning('Expired stale pending pushes', extra={'count': expired})
    return expired


def sweep_pending_pushes(batch_size=None):
    """Expire old PENDING rows, then re-dispatch recent stale ones."""
    from marketplace.models import Notification
    from marketplace.tasks import claim_push, dispatch_claimed_push

    if not getattr(settings, 'EXPO_PUSH_ENABLED', False):
        return {'expired': 0, 'dispatched': 0}
    expired = expire_stale_pending_pushes()
    from marketplace.tasks import PUSH_CLAIM_TTL
    stale_before = timezone.now() - PUSH_CLAIM_TTL
    limit = batch_size or getattr(settings, 'PUSH_INPROCESS_SWEEP_BATCH_SIZE', 25)
    candidates = list(
        Notification.objects.filter(push_status=Notification.PushStatus.PENDING, created_at__lt=stale_before)
        .order_by('created_at').values_list('id', flat=True)[:limit]
    )
    dispatched = 0
    for notification_id in candidates:
        if claim_push(notification_id):  # a live sender still owns unexpired claims
            dispatch_claimed_push(notification_id)
            dispatched += 1
    if candidates:
        logger.info('In-process push recovery sweep', extra={'candidates': len(candidates), 'dispatched': dispatched})
    return {'expired': expired, 'dispatched': dispatched}



def check_pending_receipts(now=None, batch_size=None):
    """Resolve Expo tickets into APNs/FCM receipts without Celery.

    Direct mode skipped receipt checks, so a push that Apple/Google refused
    (bad token, credentials) looked exactly like a delivered one and failed
    silently on the customer's phone. Tickets are checked once they are at
    least PUSH_RECEIPT_DELAY_SECONDS old, until a receipt is available.
    """
    from celery.exceptions import Retry
    from marketplace.models import PushDelivery
    from marketplace.tasks import process_push_receipts

    now = now or timezone.now()
    delay = timedelta(seconds=getattr(settings, 'PUSH_RECEIPT_DELAY_SECONDS', 60))
    limit = batch_size or getattr(settings, 'PUSH_INPROCESS_SWEEP_BATCH_SIZE', 25)
    notification_ids = list(
        PushDelivery.objects.filter(
            status=PushDelivery.Status.TICKET_OK, ticket_id__isnull=False, created_at__lt=now - delay,
            created_at__gte=now - timedelta(days=1),  # Expo keeps receipts for ~24 h
        ).values_list('notification_id', flat=True).distinct()[:limit]
    )
    resolved = 0
    for notification_id in notification_ids:
        try:
            resolved += process_push_receipts.run(str(notification_id)) or 0
        except Retry:
            continue  # receipt not ready yet; the next sweep asks again
        except Exception:
            logger.exception('Push receipt check failed', extra={'notification_id': str(notification_id)})
    return resolved


def _due():
    global _next_run_at
    interval = getattr(settings, 'PUSH_INPROCESS_SWEEP_SECONDS', 120)
    with _lock:
        now = time.monotonic()
        if now < _next_run_at:
            return False
        _next_run_at = now + interval
        return True


def _run_in_background():
    try:
        sweep_pending_pushes()
        check_pending_receipts()
    except Exception:  # never let recovery affect request handling
        logger.exception('In-process push recovery sweep failed')
    finally:
        connection.close()


def maybe_sweep_after_request(sender=None, **kwargs):
    """request_finished receiver. Celery beat owns recovery when enabled."""
    if not getattr(settings, 'PUSH_INPROCESS_SWEEP_ENABLED', True):
        return
    if getattr(settings, 'PUSH_USE_CELERY', False):
        return
    if not _due():
        return
    threading.Thread(target=_run_in_background, name='push-recovery-sweep', daemon=True).start()
