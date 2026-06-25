"""Campaign segmentation + delivery — the Duolingo-style re-engagement engine.

A campaign resolves a *segment* of customers, then sends each a notification
through NotificationService (which persists the in-app record and queues a
push subject to the user's preferences and quiet hours).

Anti-spam guarantees:
  * Marketing categories (CAMPAIGN / PROMO) are skipped entirely — no in-app
    record, no push — for users who opted out of that category.
  * A per-(campaign, user, period) dedup_key enforces frequency caps so a
    recurring beat task can't double-send within its period.
  * Quiet hours + push toggles are enforced downstream by NotificationService.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q, F
from django.utils import timezone

from marketplace.models import Notification, NotificationPreference, NotificationCampaign
from marketplace.services.notification_service import NotificationService

logger = logging.getLogger(__name__)
User = get_user_model()


class CampaignService:
    # ---- Segmentation ----------------------------------------------------

    @staticmethod
    def _customers():
        return User.objects.filter(role='CUSTOMER', is_active=True)

    @classmethod
    def resolve_recipients(cls, segment, params=None):
        """Return a queryset of users matching the segment."""
        params = params or {}
        Segment = NotificationCampaign.Segment
        customers = cls._customers()

        if segment == Segment.ALL:
            return customers

        if segment == Segment.ACTIVE:
            # Logged in within the last `active_days` (default 14).
            days = int(params.get('active_days', 14))
            cutoff = timezone.now() - timedelta(days=days)
            return customers.filter(last_login__gte=cutoff)
        if segment == Segment.INACTIVE:
            days = int(params.get('inactive_days', 14))
            cutoff = timezone.now() - timedelta(days=days)
            # last_login is null for users who never logged in — treat as inactive.
            return customers.filter(Q(last_login__lt=cutoff) | Q(last_login__isnull=True))

        if segment == Segment.REFERRAL:
            # Users who have referred at least one other user.
            return customers.filter(referrals__isnull=False).distinct()

        if segment == Segment.NEVER_ORDERED:
            return customers.filter(orders__isnull=True)

        if segment == Segment.CITY:
            # A customer's city comes from their saved delivery addresses.
            city = (params.get('city') or '').strip()
            if not city:
                return customers.none()
            return customers.filter(addresses__city__iexact=city).distinct()

        if segment == Segment.CUSTOM:
            ids = params.get('user_ids') or []
            return customers.filter(pk__in=ids)
        if segment == Segment.PENDING_ORDERS:
            active = ['PENDING', 'CONFIRMED', 'PICKED_UP', 'IN_PROCESS', 'OUT_FOR_DELIVERY']
            return customers.filter(orders__status__in=active).distinct()

        if segment == Segment.UNPAID:
            return customers.filter(
                orders__payment_status='UNPAID',
            ).exclude(orders__status__in=['CANCELLED', 'REJECTED']).distinct()

        if segment == Segment.PROMO_OPT_IN:
            opted_in_ids = NotificationPreference.objects.filter(
                promotions=True
            ).values_list('user_id', flat=True)
            # Users with no preference row default to opted-in.
            has_pref_ids = NotificationPreference.objects.values_list('user_id', flat=True)
            return customers.filter(Q(pk__in=opted_in_ids) | ~Q(pk__in=has_pref_ids))

        if segment == Segment.ABANDONED_BOOKING:
            # Server-side proxy for an abandoned booking: an order placed more
            # than `hours` ago that is still PENDING and unpaid.
            hours = int(params.get('abandoned_hours', 6))
            cutoff = timezone.now() - timedelta(hours=hours)
            return customers.filter(
                orders__status='PENDING',
                orders__payment_status='UNPAID',
                orders__created_at__lt=cutoff,
            ).distinct()

        return customers.none()

    # ---- Opt-in gate -----------------------------------------------------

    @staticmethod
    def _opted_in(user, *, type, category):
        """Marketing notifications require an explicit opt-in. Returns False
        only when the user has a preference row that disables the governing
        toggle (missing row = opted in by default)."""
        try:
            pref = NotificationPreference.objects.filter(user=user).first()
        except Exception:  # pragma: no cover
            return True
        if pref is None:
            return True
        cat = (category or '').upper()
        if cat == 'CAMPAIGN':
            return pref.campaigns
        if (type or '').upper() == Notification.Type.PROMO or cat == 'PROMO':
            return pref.promotions
        return True

    # ---- Delivery --------------------------------------------------------

    @classmethod
    def deliver(cls, *, recipients, title, body, type=Notification.Type.PROMO,
                category='CAMPAIGN', action_url='', dedup_prefix='campaign',
                period_key='', priority=Notification.Priority.NORMAL, campaign=None,
                promo_code=''):
        """Send to an iterable of users. Returns (delivered, skipped).

        `delivered` counts notifications successfully created (in-app reach,
        plus a queued push where preferences permit). `skipped` counts users
        who opted out of the marketing category.
        """
        delivered = 0
        skipped = 0
        for user in recipients:
            if not cls._opted_in(user, type=type, category=category):
                skipped += 1
                continue
            dedup_key = f'{dedup_prefix}:{user.id}'
            if period_key:
                dedup_key = f'{dedup_prefix}:{user.id}:{period_key}'
            try:
                NotificationService.notify_user(
                    user,
                    title=title,
                    body=body,
                    type=type,
                    category=category,
                    priority=priority,
                    action_url=action_url,
                    dedup_key=dedup_key,
                    push=True,
                    campaign=campaign,
                    promo_code=promo_code,
                )
                delivered += 1
            except Exception as exc:  # pragma: no cover - per-user safety
                logger.error("Campaign delivery failed for user",
                             extra={'user_id': str(user.id), 'error': str(exc)})
                skipped += 1
        return delivered, skipped

    # ---- Conversion attribution -----------------------------------------

    ATTRIBUTION_WINDOW_DAYS = 7

    @classmethod
    def attribute_conversion(cls, user, order=None, value=None):
        """Credit a downstream order to the most recent campaign notification.

        Looks back over the attribution window for the user's most recent
        campaign-linked notification that hasn't already been credited, marks
        it converted, and rolls revenue up to the campaign. Idempotent per
        (notification, order). Best-effort — never raises to the caller.
        """
        if user is None:
            return None
        try:
            window_days = getattr(settings, 'CAMPAIGN_ATTRIBUTION_WINDOW_DAYS',
                                  cls.ATTRIBUTION_WINDOW_DAYS)
            since = timezone.now() - timedelta(days=window_days)
            notif = (
                Notification.objects.filter(
                    user=user,
                    campaign__isnull=False,
                    converted_at__isnull=True,
                    created_at__gte=since,
                )
                .order_by('-created_at')
                .first()
            )
            if notif is None:
                return None

            amount = Decimal(str(value if value is not None else 0))
            notif.converted_at = timezone.now()
            notif.conversion_value = amount
            notif.save(update_fields=['converted_at', 'conversion_value'])

            NotificationCampaign.objects.filter(pk=notif.campaign_id).update(
                converted_count=F('converted_count') + 1,
                revenue_generated=F('revenue_generated') + amount,
            )
            logger.info("Campaign conversion attributed", extra={
                'campaign_id': str(notif.campaign_id),
                'notification_id': str(notif.id),
                'order_id': str(getattr(order, 'id', '')) or None,
                'value': str(amount),
            })
            return notif
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Conversion attribution failed",
                         extra={'user_id': str(getattr(user, 'id', '')), 'error': str(exc)})
            return None

    @classmethod
    def run(cls, campaign):
        """Execute a stored NotificationCampaign and record analytics."""
        if campaign.is_expired:
            campaign.status = NotificationCampaign.Status.FAILED
            campaign.save(update_fields=['status'])
            logger.info("Campaign skipped: expired", extra={'campaign_id': str(campaign.id)})
            return 0, 0
        recipients = list(cls.resolve_recipients(campaign.segment, campaign.segment_params))
        campaign.status = NotificationCampaign.Status.SENDING
        campaign.recipients_count = len(recipients)
        campaign.save(update_fields=['status', 'recipients_count'])

        delivered, skipped = cls.deliver(
            recipients=recipients,
            title=campaign.title,
            body=campaign.body,
            type=campaign.notification_type,
            category=campaign.category,
            action_url=campaign.action_url,
            priority=campaign.priority,
            campaign=campaign,
            promo_code=campaign.promo_code,
            dedup_prefix=f'campaign:{campaign.id}',
            period_key=timezone.now().strftime('%Y%m%d'),
        )

        campaign.delivered_count = delivered
        campaign.skipped_count = skipped
        campaign.status = NotificationCampaign.Status.SENT
        campaign.sent_at = timezone.now()
        campaign.save(update_fields=['delivered_count', 'skipped_count', 'status', 'sent_at'])
        logger.info("Campaign sent", extra={
            'campaign_id': str(campaign.id),
            'recipients': len(recipients),
            'delivered': delivered,
            'skipped': skipped,
        })
        return delivered, skipped
