"""
Tell a laundry's own customers when it starts a free pickup/delivery promo.

Only people with a relationship to the laundry are told: customers who saved it
as a favourite and customers who have ordered from it before. The push goes
through ``CampaignService.deliver``, which honours each customer's
"promotions" toggle and quiet hours, and de-duplicates per campaign.

Each campaign is announced at most once. The laundry row is claimed with a
conditional UPDATE, so two workers saving the same laundry cannot both send,
and a cooldown stops an owner from re-notifying everyone by toggling the promo
off and on.
"""

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

#: A laundry can announce a new campaign at most this often.
ANNOUNCE_COOLDOWN = timedelta(days=7)
#: Upper bound on one announcement, so a popular laundry cannot fan out unbounded.
MAX_RECIPIENTS = 1000


def promo_recipients(laundry):
    from django.contrib.auth import get_user_model
    from laundries.models.favorite import Favorite
    from ordering.models import Order

    User = get_user_model()
    favourite_ids = Favorite.objects.filter(laundry=laundry).values_list('user_id', flat=True)
    customer_ids = (
        Order.objects.filter(laundry=laundry)
        .exclude(status__in=[Order.Status.CANCELLED, Order.Status.REJECTED])
        .values_list('user_id', flat=True)
    )
    return (
        User.objects.filter(is_active=True, role='CUSTOMER')
        .filter(id__in=set(favourite_ids) | set(customer_ids))
        .exclude(id=laundry.owner_id)
        .order_by('id')[:MAX_RECIPIENTS]
    )


def promo_message(laundry):
    covers = {
        laundry.PromoScope.PICKUP_ONLY: 'free pickup',
        laundry.PromoScope.DELIVERY_ONLY: 'free delivery',
    }.get(laundry.promo_scope, 'free pickup & delivery')
    title = f"{laundry.name} now has {covers} \U0001F389"
    now = timezone.now()
    if laundry.promo_start_at and laundry.promo_start_at > now:
        when = timezone.localtime(laundry.promo_start_at).strftime('%d %b')
        title = f"{laundry.name}: {covers} from {when} \U0001F389"
    body = f"Book with {laundry.name} and pay nothing for {covers.replace('free ', '')}."
    if laundry.promo_end_at:
        body += f" Offer ends {timezone.localtime(laundry.promo_end_at).strftime('%d %b')}."
    if laundry.promo_min_order_value:
        body += f" Orders from GHS {laundry.promo_min_order_value}."
    return title, body


def _claim(laundry_id, campaign_id):
    """
    Mark this campaign announced. Returns the laundry when this caller won the
    claim and should send, else None.
    """
    from laundries.models.laundry import Laundry

    laundry = Laundry.objects.filter(pk=laundry_id).first()
    if laundry is None or laundry.promo_campaign_id != campaign_id:
        return None  # a newer campaign replaced this one
    if not laundry.free_delivery_promo_enabled:
        return None
    now = timezone.now()
    if laundry.promo_end_at and laundry.promo_end_at < now:
        return None
    if laundry.status != Laundry.ApprovalStatus.APPROVED or not laundry.is_active:
        return None

    in_cooldown = bool(
        laundry.promo_last_notified_at and now - laundry.promo_last_notified_at < ANNOUNCE_COOLDOWN
    )
    claimed = (
        Laundry.objects.filter(pk=laundry_id, promo_campaign_id=campaign_id)
        .exclude(promo_notified_campaign_id=campaign_id)
        .update(
            promo_notified_campaign_id=campaign_id,
            promo_last_notified_at=laundry.promo_last_notified_at if in_cooldown else now,
        )
    )
    if not claimed:
        return None
    if in_cooldown:
        logger.info(
            "Promo announcement skipped: cooldown",
            extra={"laundry_id": str(laundry_id), "campaign_id": str(campaign_id)},
        )
        return None
    return laundry


def send_promo_announcement(laundry_id, campaign_id):
    """Claim and send. Returns (delivered, skipped), or None when not sent."""
    from marketplace.models import Notification
    from marketplace.services.campaign_service import CampaignService

    laundry = _claim(laundry_id, campaign_id)
    if laundry is None:
        return None
    title, body = promo_message(laundry)
    delivered, skipped = CampaignService.deliver(
        recipients=promo_recipients(laundry),
        title=title,
        body=body,
        type=Notification.Type.PROMO,
        category='PROMO',
        action_url=f'/laundry-details?id={laundry.id}',
        dedup_prefix=f'promo_free_transport:{campaign_id}',
    )
    logger.info(
        "Promo announcement sent",
        extra={
            "laundry_id": str(laundry_id),
            "campaign_id": str(campaign_id),
            "delivered": delivered,
            "skipped": skipped,
        },
    )
    return delivered, skipped


def announce_promo_campaign(laundry_id, campaign_id):
    """
    Entry point used after a laundry save commits. Runs off the request thread
    in production so an owner's "save" never waits on hundreds of pushes.
    """
    def _run():
        try:
            send_promo_announcement(laundry_id, campaign_id)
        except Exception:
            logger.exception(
                "Promo announcement failed",
                extra={"laundry_id": str(laundry_id), "campaign_id": str(campaign_id)},
            )

    if getattr(settings, 'PUSH_DISPATCH_IN_THREAD', True):
        def _in_thread():
            try:
                _run()
            finally:
                connection.close()
        threading.Thread(target=_in_thread, daemon=False).start()
    else:
        _run()
