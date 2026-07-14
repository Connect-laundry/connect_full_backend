"""Pluggable delivery providers for the notification layer.

``NotificationService`` (notification_service.py) owns in-app notifications;
these providers own *delivery channels*. Email works out of the box through
Django's email backend. SMS stays disabled until ``settings.SMS_PROVIDER`` is
configured with a concrete implementation. Push delegates to the existing Expo
pipeline.

Usage:
    from marketplace.services.providers import get_email_provider, get_sms_provider
    get_email_provider().send(to=[...], subject=..., text=..., html=...)
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class EmailProvider:
    """Sends email via the configured Django EMAIL_BACKEND."""

    def is_configured(self) -> bool:
        return bool(getattr(settings, 'EMAIL_HOST', None)) or bool(
            getattr(settings, 'EMAIL_BACKEND', '').endswith(('locmem.EmailBackend', 'console.EmailBackend'))
        )

    def send(self, *, to, subject, text, html=None, from_email=None):
        from django.core.mail import EmailMultiAlternatives

        msg = EmailMultiAlternatives(
            subject,
            text,
            from_email or settings.DEFAULT_FROM_EMAIL,
            list(to),
        )
        if html:
            msg.attach_alternative(html, "text/html")
        msg.send()
        return True


class SMSProvider:
    """SMS delivery. Disabled until ``settings.SMS_PROVIDER`` names a backend.

    To plug a provider in later, subclass and register it in ``_SMS_BACKENDS``
    (e.g. ``'hubtel': HubtelSMSProvider``) — no call sites change.
    """

    def is_configured(self) -> bool:
        return False

    def send(self, *, to, body):
        logger.info(
            "SMS delivery skipped (no SMS_PROVIDER configured)",
            extra={"recipients": len(list(to))},
        )
        return False


class PushProvider:
    """Push delivery via the existing Expo push pipeline."""

    def is_configured(self) -> bool:
        return bool(getattr(settings, 'EXPO_PUSH_ENABLED', False))

    def send(self, *, notification_id):
        from marketplace.tasks import send_real_push
        from utils.tasks import safe_task_delay
        return safe_task_delay(send_real_push, str(notification_id))


_SMS_BACKENDS = {
    # 'twilio': TwilioSMSProvider,   # future
    # 'hubtel': HubtelSMSProvider,   # future
}


def get_email_provider() -> EmailProvider:
    return EmailProvider()


def get_sms_provider() -> SMSProvider:
    backend = getattr(settings, 'SMS_PROVIDER', '')
    provider_cls = _SMS_BACKENDS.get(backend, SMSProvider)
    return provider_cls()


def get_push_provider() -> PushProvider:
    return PushProvider()
