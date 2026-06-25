"""Unified notification service — the single source of truth for creating
notifications across every audience (mobile users and admins).

Every domain event (new order, payment, laundry approval, ...) should call this
service exactly once; it persists the DB record and, for user notifications,
queues the existing Expo push task. Admin notifications are surfaced via the
admin bell/polling API rather than push.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from marketplace.models import Notification, NotificationPreference

logger = logging.getLogger(__name__)
User = get_user_model()


class NotificationService:
    @staticmethod
    def _dedup_exists(audience, dedup_key, user=None):
        if not dedup_key:
            return None
        qs = Notification.objects.filter(audience=audience, dedup_key=dedup_key, is_read=False)
        if user is not None:
            qs = qs.filter(user=user)
        return qs.first()

    @staticmethod
    def get_preferences(user):
        """Return (creating if needed) the user's NotificationPreference."""
        pref, _ = NotificationPreference.objects.get_or_create(user=user)
        return pref

    @classmethod
    def _push_permitted(cls, user, *, type, category, priority):
        """Decide whether a push may be sent, honouring per-user toggles and
        quiet hours. In-app persistence is unaffected — this only gates push.

        URGENT notifications bypass quiet hours (but still respect an explicit
        category opt-out / master push-off)."""
        try:
            pref = cls.get_preferences(user)
        except Exception:  # pragma: no cover - never block on pref lookup
            return True

        if not pref.allows_push(type=type, category=category):
            return False

        if priority != Notification.Priority.URGENT:
            local_hour = timezone.localtime(timezone.now()).hour
            if pref.in_quiet_hours(local_hour):
                return False

        return True

    @classmethod
    def notify_user(
        cls,
        user,
        title,
        body,
        *,
        type=Notification.Type.SYSTEM,
        category='',
        priority=Notification.Priority.NORMAL,
        action_url='',
        related_order=None,
        dedup_key='',
        push=True,
    ):
        """Create a USER-audience notification and (optionally) queue a push."""
        if user is None:
            return None

        existing = cls._dedup_exists(Notification.Audience.USER, dedup_key, user=user)
        if existing:
            return existing

        notification = Notification.objects.create(
            user=user,
            audience=Notification.Audience.USER,
            title=title,
            body=body,
            type=type,
            category=category,
            priority=priority,
            action_url=action_url,
            related_order=related_order,
            dedup_key=dedup_key,
        )

        if (
            push
            and getattr(settings, 'EXPO_PUSH_ENABLED', False)
            and cls._push_permitted(user, type=type, category=category, priority=priority)
        ):
            cls._queue_push(notification.id)

        return notification

    @classmethod
    def notify_admins(
        cls,
        title,
        body,
        *,
        category='',
        priority=Notification.Priority.NORMAL,
        action_url='',
        type=Notification.Type.SYSTEM,
        related_order=None,
        dedup_key='',
    ):
        """Create a single ADMIN-audience notification (visible to all admins)."""
        existing = cls._dedup_exists(Notification.Audience.ADMIN, dedup_key)
        if existing:
            return existing

        return Notification.objects.create(
            user=None,
            audience=Notification.Audience.ADMIN,
            title=title,
            body=body,
            type=type,
            category=category,
            priority=priority,
            action_url=action_url,
            related_order=related_order,
            dedup_key=dedup_key,
        )

    @classmethod
    def system_alert(cls, title, body, *, category='SYSTEM_ALERT', priority=None,
                     action_url='', dedup_key=''):
        """Convenience for infra/security alerts (Celery/Redis/health, deploy,
        backup, security). Surfaced to admins; deduped via dedup_key so an
        ongoing outage collapses into a single unread notification."""
        if priority is None:
            priority = Notification.Priority.HIGH
        return cls.notify_admins(
            title, body, category=category, priority=priority,
            action_url=action_url, dedup_key=dedup_key,
        )

    @staticmethod
    def _queue_push(notification_id):
        try:
            # Imported lazily to avoid circular imports at app load.
            from marketplace.tasks import send_real_push
            send_real_push.delay(str(notification_id))
        except Exception as exc:  # pragma: no cover - broker/runtime safety
            logger.error(
                "Failed to queue push notification",
                extra={"notification_id": str(notification_id), "error": str(exc)},
            )
