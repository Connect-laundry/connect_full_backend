# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
import logging
import requests
from django.conf import settings
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


def deliver_push(title, body, data, tokens):
    """Send one Expo push batch and clean up invalid tokens from the receipt.

    Returns the number of messages accepted by Expo. Tokens that Expo reports
    as `DeviceNotRegistered` (or otherwise invalid) are deactivated so we stop
    pushing to dead devices. Safe to call from any task — never raises for
    per-token errors; only network failures propagate (to allow Celery retry).
    """
    valid_tokens = [t for t in tokens if t and _is_expo_token(t)]
    if not valid_tokens:
        return 0

    messages = [
        {"to": token, "sound": "default", "title": title, "body": body, "data": data or {}}
        for token in valid_tokens
    ]

    response = requests.post(
        "https://exp.host/--/api/v2/push/send",
        json=messages,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=10,
    )
    response.raise_for_status()

    # Inspect per-message tickets and reap dead tokens.
    try:
        tickets = response.json().get('data', [])
        dead = []
        for token, ticket in zip(valid_tokens, tickets):
            if not isinstance(ticket, dict):
                continue
            if ticket.get('status') == 'error':
                code = (ticket.get('details') or {}).get('error')
                if code == 'DeviceNotRegistered':
                    dead.append(token)
        _deactivate_tokens(dead)
    except (ValueError, KeyError) as exc:  # pragma: no cover - malformed response
        logger.warning("Could not parse Expo push receipt", extra={"error": str(exc)})

    return len(messages)


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
        }
        sent = deliver_push(notification.title, notification.body, data, tokens)
        if sent:
            logger.info(
                "Push notifications sent",
                extra={"notification_id": str(notification.id), "count": sent},
            )
        return sent
    except Notification.DoesNotExist:
        pass
    except requests.RequestException as e:
        logger.error("Push delivery failed", extra={"error": summarize_exception(e)})
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


@shared_task(name="marketplace.tasks.enqueue_rainy_day_promo")
def enqueue_rainy_day_promo():
    """Create and queue a rainy-day promo campaign when the weather feed says rain is likely."""
    from marketplace.services.weather_campaign import WeatherCampaignService

    campaign = WeatherCampaignService.enqueue_rainy_day_campaign()
    return str(campaign.id) if campaign else None
