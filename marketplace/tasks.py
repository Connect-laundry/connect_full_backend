# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
import logging
import requests
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model
# pyre-ignore[missing-module]
from marketplace.models import Notification, PushDevice
# pyre-ignore[missing-module]
from django.core.exceptions import ObjectDoesNotExist
from config.redaction import summarize_exception

User = get_user_model()
logger = logging.getLogger(__name__)

@shared_task(
    name="marketplace.tasks.create_notification",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 5}
)
def create_notification(self, user_id, title, body, notification_type='SYSTEM', related_order_id=None):
    """
    Asynchronously creates a notification record in the database.
    This can be extended to trigger real push notifications (FCM/OneSignal).
    """
    try:
        user = User.objects.get(id=user_id)
        notification = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            type=notification_type,
            related_order_id=related_order_id
        )
        
        send_real_push.delay(str(notification.id))
        
        logger.info(
            "Notification created",
            extra={"user_id": str(user.id), "notification_id": str(notification.id)},
        )
        return str(notification.id)
    except User.DoesNotExist:
        logger.error("Failed to create notification: user not found", extra={"user_id": str(user_id)})
    except Exception as e:
        logger.error("Error creating notification", extra={"error": summarize_exception(e)})
    return None

def _is_expo_token(token):
    return token.startswith('ExponentPushToken[') or token.startswith('ExpoPushToken[')


def _deactivate_tokens(tokens):
    """Mark push tokens inactive (Expo reported them as unregistered/invalid)."""
    if not tokens:
        return 0
    updated = PushDevice.objects.filter(token__in=tokens, is_active=True).update(is_active=False)
    if updated:
        logger.info("Deactivated stale push tokens", extra={"count": updated})
    return updated


EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
# Expo accepts at most 100 messages per /push/send call.
EXPO_BATCH_SIZE = 100


def expo_push_headers():
    """Headers for the Expo push API, including the access token when set.

    Expo projects with "Enhanced Security for Push Notifications" enabled
    reject unauthenticated sends, so ``EXPO_ACCESS_TOKEN`` must be configured
    for those projects or nothing reaches a device.
    """
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = getattr(settings, 'EXPO_ACCESS_TOKEN', '')
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# Must match ANDROID_CHANNELS in the app's pushNotification.service.ts. The
# ids are versioned because Android freezes a channel's importance at creation.
ANDROID_CHANNEL_DEFAULT = 'default_v2'
ANDROID_CHANNEL_ORDERS = 'orders_v2'


def channel_for(category, notification_type):
    """Android channel to deliver on.

    Android decides whether a notification interrupts the user from the
    *channel*, not the message — so a transactional update must land on the
    HIGH-importance `orders` channel or it will sit silently in the tray with
    no heads-up banner.
    """
    signal = f"{category or ''} {notification_type or ''}".upper()
    if any(word in signal for word in ('ORDER', 'PAYMENT', 'DELIVERY', 'PICKUP')):
        return ANDROID_CHANNEL_ORDERS
    return ANDROID_CHANNEL_DEFAULT


def deliver_push(title, body, data, tokens, *, channel_id=ANDROID_CHANNEL_DEFAULT, badge=None):
    """Send Expo push batches and clean up invalid tokens from the tickets.

    Returns the number of messages Expo accepted. Tokens Expo reports as
    `DeviceNotRegistered` (or otherwise invalid) are deactivated so we stop
    pushing to dead devices. Safe to call from any task — never raises for
    per-token errors; only network failures propagate (to allow Celery retry).
    """
    valid_tokens = [t for t in tokens if t and _is_expo_token(t)]
    if not valid_tokens:
        return 0

    accepted = 0
    dead = []
    ticket_ids = []

    for start in range(0, len(valid_tokens), EXPO_BATCH_SIZE):
        batch = valid_tokens[start:start + EXPO_BATCH_SIZE]
        messages = [
            {
                "to": token,
                "sound": "default",
                "title": title,
                "body": body,
                "data": data or {},
                # Without high priority Expo sends APNs priority 5, which iOS
                # is free to delay or batch — the notification may never
                # visibly arrive.
                "priority": "high",
                # Android: picks the channel's importance. 'orders' is HIGH,
                # so transactional pushes get a heads-up banner.
                "channelId": channel_id,
                # iOS 15+: without this, Focus modes and the scheduled
                # Notification Summary can hold the alert back silently.
                "interruptionLevel": "active",
                # Keep it deliverable for a day if the device is offline.
                "ttl": 86400,
                **({"badge": badge} if isinstance(badge, int) else {}),
                # image_url is included when provided (iOS shows as attachment preview).
                **({"image": data.get("imageUrl")} if isinstance(data, dict) and data.get("imageUrl") else {}),
            }
            for token in batch
        ]

        response = requests.post(
            EXPO_PUSH_URL,
            json=messages,
            headers=expo_push_headers(),
            timeout=10,
        )

        # Expo returns structured errors (e.g. an invalid or missing access
        # token) in the body; surface them instead of a bare status code.
        if response.status_code >= 400:
            detail = response.text[:500]
            logger.error(
                "Expo push API rejected the request",
                extra={"status": response.status_code, "detail": detail},
            )
            response.raise_for_status()

        try:
            tickets = response.json().get('data', [])
        except ValueError as exc:  # pragma: no cover - malformed response
            logger.warning("Could not parse Expo push response", extra={"error": str(exc)})
            continue

        for token, ticket in zip(batch, tickets):
            if not isinstance(ticket, dict):
                continue
            if ticket.get('status') == 'error':
                code = (ticket.get('details') or {}).get('error')
                # 'message' is reserved on LogRecord; use a distinct key.
                logger.warning(
                    "Expo rejected a push message",
                    extra={"error": code, "expo_message": ticket.get('message')},
                )
                if code == 'DeviceNotRegistered':
                    dead.append(token)
                continue
            accepted += 1
            if ticket.get('id'):
                ticket_ids.append(ticket['id'])

    _deactivate_tokens(dead)
    return accepted


def fetch_push_receipts(ticket_ids):
    """Resolve Expo ticket ids to delivery receipts.

    A ticket only means Expo queued the message; the receipt says whether
    APNs/FCM accepted it. Deactivates tokens reported as `DeviceNotRegistered`
    is not possible here (receipts are keyed by ticket), so callers log.
    """
    if not ticket_ids:
        return {}
    response = requests.post(
        EXPO_RECEIPTS_URL,
        json={"ids": list(ticket_ids)},
        headers=expo_push_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get('data', {}) or {}


@shared_task(
    name="marketplace.tasks.send_real_push",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 7}
)
def send_real_push(self, notification_id):
    """
    Sends the notification to registered Expo push tokens.
    """
    try:
        notification = Notification.objects.select_related('user').get(id=notification_id)
        if not getattr(settings, 'EXPO_PUSH_ENABLED', False):
            return 0

        tokens = list(
            PushDevice.objects.filter(user=notification.user, is_active=True)
            .values_list('token', flat=True)
        )
        data = {
            "notificationId": str(notification.id),
            "type": notification.type,
            "category": notification.category,
            "relatedOrder": str(notification.related_order_id) if notification.related_order_id else None,
            "actionUrl": notification.action_url or None,
            "promoCode": notification.promo_code or None,
            # Pass campaign image_url so iOS can render a rich attachment.
            "imageUrl": (
                notification.campaign.image_url
                if notification.campaign_id and notification.campaign.image_url
                else None
            ),
        }
        # Unread count drives the OS app-icon badge, so it stays correct even
        # when the app is killed and never opens to refresh it.
        badge = Notification.objects.filter(
            user=notification.user,
            audience=Notification.Audience.USER,
            is_read=False,
        ).count()

        sent = deliver_push(
            notification.title,
            notification.body,
            data,
            tokens,
            channel_id=channel_for(notification.category, notification.type),
            badge=badge,
        )
        if sent:
            notification.push_status = Notification.PushStatus.SENT
            notification.delivered_at = timezone.now()
            notification.save(update_fields=['push_status', 'delivered_at'])
            logger.info(
                "Push notifications sent",
                extra={"notification_id": str(notification.id), "count": sent},
            )
        return sent
    except Notification.DoesNotExist:
        pass
    except requests.RequestException as e:
        logger.error("Push delivery failed", extra={"error": summarize_exception(e)})
        # On the final attempt, mark the notification failed and roll the
        # failure up to its campaign (idempotent: only counts once, at exhaustion).
        if self.request.retries >= self.max_retries:
            try:
                notif = Notification.objects.filter(id=notification_id).first()
                if notif and notif.push_status != Notification.PushStatus.FAILED:
                    notif.push_status = Notification.PushStatus.FAILED
                    notif.save(update_fields=['push_status'])
                    if notif.campaign_id:
                        from django.db.models import F
                        Notification.campaign.field.related_model.objects.filter(
                            pk=notif.campaign_id
                        ).update(failed_count=F('failed_count') + 1)
            except Exception:  # pragma: no cover - defensive
                pass
        raise
    return 0


# ---------------------------------------------------------------------------
# Campaign / re-engagement tasks (Duolingo-style)
#
# Each task resolves a customer segment via CampaignService and delivers a
# preference- and quiet-hours-aware notification. dedup_keys carry a period
# bucket so recurring beat schedules act as frequency caps rather than spam.
# ---------------------------------------------------------------------------


@shared_task(name="marketplace.tasks.run_campaign", bind=True,
             autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def run_campaign(self, campaign_id):
    """Execute a stored NotificationCampaign by id (admin-triggered or scheduled)."""
    from marketplace.models import NotificationCampaign
    from marketplace.services.campaign_service import CampaignService
    try:
        campaign = NotificationCampaign.objects.get(id=campaign_id)
    except NotificationCampaign.DoesNotExist:
        logger.warning("run_campaign: campaign not found", extra={"campaign_id": str(campaign_id)})
        return 0
    delivered, _ = CampaignService.run(campaign)
    return delivered


@shared_task(name="marketplace.tasks.weekly_pending_orders_reminder")
def weekly_pending_orders_reminder():
    """Nudge customers who still have an order in progress. Weekly cap."""
    from marketplace.models import Notification, NotificationCampaign
    from marketplace.services.campaign_service import CampaignService
    from django.utils import timezone

    recipients = CampaignService.resolve_recipients(NotificationCampaign.Segment.PENDING_ORDERS)
    iso_year, iso_week, _ = timezone.now().isocalendar()
    delivered, skipped = CampaignService.deliver(
        recipients=recipients,
        title="Your laundry is in motion",
        body="You have an order in progress. Tap to check its latest status.",
        type=Notification.Type.ORDER,
        category='CAMPAIGN',
        action_url='/orders',
        dedup_prefix='weekly_pending',
        period_key=f'{iso_year}W{iso_week}',
    )
    logger.info("weekly_pending_orders_reminder", extra={"delivered": delivered, "skipped": skipped})
    return delivered


@shared_task(name="marketplace.tasks.inactivity_reactivation")
def inactivity_reactivation(inactive_days=14):
    """Win back customers who haven't opened the app in `inactive_days`.
    Monthly cap per user so we never nag."""
    from marketplace.models import Notification, NotificationCampaign
    from marketplace.services.campaign_service import CampaignService
    from django.utils import timezone

    recipients = CampaignService.resolve_recipients(
        NotificationCampaign.Segment.INACTIVE, {'inactive_days': inactive_days}
    )
    delivered, skipped = CampaignService.deliver(
        recipients=recipients,
        title="We miss you! 👕",
        body="It's been a while. Fresh, clean laundry is just a tap away — book a pickup today.",
        type=Notification.Type.PROMO,
        category='CAMPAIGN',
        action_url='/home',
        dedup_prefix='inactivity',
        period_key=timezone.now().strftime('%Y%m'),
    )
    logger.info("inactivity_reactivation", extra={"delivered": delivered, "skipped": skipped})
    return delivered


@shared_task(name="marketplace.tasks.abandoned_booking_reminder")
def abandoned_booking_reminder(abandoned_hours=6):
    """Remind customers who placed an order but never paid for it."""
    from marketplace.models import Notification, NotificationCampaign
    from marketplace.services.campaign_service import CampaignService
    from django.utils import timezone

    recipients = CampaignService.resolve_recipients(
        NotificationCampaign.Segment.ABANDONED_BOOKING, {'abandoned_hours': abandoned_hours}
    )
    delivered, skipped = CampaignService.deliver(
        recipients=recipients,
        title="Finish your booking",
        body="Your order is waiting. Complete payment to get your laundry on its way.",
        type=Notification.Type.ORDER,
        category='CAMPAIGN',
        action_url='/orders',
        dedup_prefix='abandoned_booking',
        period_key=timezone.now().strftime('%Y%m%d'),
    )
    logger.info("abandoned_booking_reminder", extra={"delivered": delivered, "skipped": skipped})
    return delivered


@shared_task(name="marketplace.tasks.process_scheduled_campaigns")
def process_scheduled_campaigns():
    """Run any SCHEDULED campaign whose scheduled_for has arrived.

    Drives admin "schedule for later" sends (tomorrow 8am, Black Friday, etc.).
    Expired campaigns are marked FAILED and skipped. Each campaign is handed to
    run_campaign so a single slow campaign can't block the sweep.
    """
    from marketplace.models import NotificationCampaign

    now = timezone.now()
    due = NotificationCampaign.objects.filter(
        status=NotificationCampaign.Status.SCHEDULED,
        scheduled_for__isnull=False,
        scheduled_for__lte=now,
    )
    queued = 0
    for campaign in due:
        if campaign.is_expired:
            campaign.status = NotificationCampaign.Status.FAILED
            campaign.save(update_fields=['status'])
            continue
        run_campaign.delay(str(campaign.id))
        queued += 1
    if queued:
        logger.info("Scheduled campaigns queued", extra={"count": queued})
    return queued
@shared_task(name="marketplace.tasks.enqueue_rainy_day_promo")
def enqueue_rainy_day_promo():
    """Create and queue a rainy-day promo campaign when the weather feed says rain is likely."""
    from marketplace.services.weather_campaign import WeatherCampaignService

    campaign = WeatherCampaignService.enqueue_rainy_day_campaign()
    return str(campaign.id) if campaign else None
