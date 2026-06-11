# pyre-ignore[missing-module]
import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
# pyre-ignore[missing-module]
from django.utils import timezone

class Notification(models.Model):
    class Type(models.TextChoices):
        ORDER = 'ORDER', _('Order Update')
        SYSTEM = 'SYSTEM', _('System Message')
        PROMO = 'PROMO', _('Promotion')

    class Audience(models.TextChoices):
        USER = 'USER', _('User')
        ADMIN = 'ADMIN', _('Admin')

    class Priority(models.TextChoices):
        LOW = 'LOW', _('Low')
        NORMAL = 'NORMAL', _('Normal')
        HIGH = 'HIGH', _('High')
        URGENT = 'URGENT', _('Urgent')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Null for ADMIN-audience broadcasts (visible to every admin); set for
    # USER-audience notifications delivered to a specific account.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        null=True,
        blank=True,
    )
    audience = models.CharField(
        max_length=10, choices=Audience.choices, default=Audience.USER, db_index=True
    )
    title = models.CharField(max_length=255)
    body = models.TextField(default='')
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.SYSTEM)
    # Fine-grained event category, e.g. PAYMENT_SUCCESS, NEW_USER, LAUNDRY_PENDING.
    category = models.CharField(max_length=40, blank=True, default='', db_index=True)
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL
    )
    # Relative admin/app URL the notification deep-links to.
    action_url = models.CharField(max_length=500, blank=True, default='')
    # Optional idempotency key used by NotificationService to avoid duplicates.
    dedup_key = models.CharField(max_length=120, blank=True, default='', db_index=True)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    related_order = models.ForeignKey(
        'ordering.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Notification')
        verbose_name_plural = _('Notifications')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', 'created_at']),
            models.Index(fields=['audience', 'is_read', 'created_at']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        target = self.user.email if self.user else f"ADMIN({self.audience})"
        return f"{self.title} - {target}"

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])


class PushDevice(models.Model):
    class Platform(models.TextChoices):
        IOS = 'ios', _('iOS')
        ANDROID = 'android', _('Android')
        WEB = 'web', _('Web')
        UNKNOWN = 'unknown', _('Unknown')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='push_devices',
    )
    token = models.CharField(max_length=255, unique=True, db_index=True)
    device_id = models.CharField(max_length=128, blank=True, db_index=True)
    platform = models.CharField(max_length=20, choices=Platform.choices, default=Platform.UNKNOWN)
    app_version = models.CharField(max_length=50, blank=True)
    web_endpoint = models.TextField(blank=True, default='')
    web_p256dh = models.CharField(max_length=255, blank=True, default='')
    web_auth = models.CharField(max_length=255, blank=True, default='')
    is_active = models.BooleanField(default=True, db_index=True)
    last_registered_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['device_id']),
        ]

    def __str__(self):
        return f"{self.platform} push device for {self.user_id}"
