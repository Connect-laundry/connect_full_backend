import uuid

# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings


class AnalyticsEvent(models.Model):
    """A single product-analytics event (first-party, Mixpanel-style).

    Events arrive in batches from the mobile app (and are also emitted
    server-side for authoritative funnels like ORDER_CREATED / PAYMENT_SUCCESS).
    `event_data` is a small, PII-redacted JSON blob. There is intentionally no
    raw IP / token / password column — redaction happens before persistence.
    """

    class Platform(models.TextChoices):
        IOS = 'ios', 'iOS'
        ANDROID = 'android', 'Android'
        WEB = 'web', 'Web'
        SERVER = 'server', 'Server'
        UNKNOWN = 'unknown', 'Unknown'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_name = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_events',
    )
    # Stable per-app-launch id (client) — lets us reconstruct sessions without PII.
    session_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    device_id = models.CharField(max_length=128, blank=True, default='')
    platform = models.CharField(max_length=10, choices=Platform.choices, default=Platform.UNKNOWN)
    os_version = models.CharField(max_length=32, blank=True, default='')
    app_version = models.CharField(max_length=32, blank=True, default='')
    screen_name = models.CharField(max_length=64, blank=True, default='', db_index=True)
    event_data = models.JSONField(default=dict, blank=True)
    # When the client recorded the event (may differ from server receipt time).
    occurred_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Analytics Event'
        verbose_name_plural = 'Analytics Events'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['event_name', 'created_at']),
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['session_id', 'created_at']),
        ]

    def __str__(self):
        return f"{self.event_name} @ {self.created_at:%Y-%m-%d %H:%M}"
