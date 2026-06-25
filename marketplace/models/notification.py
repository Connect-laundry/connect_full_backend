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

    class PushStatus(models.TextChoices):
        NONE = 'NONE', _('No push')          # in-app only / push not attempted
        PENDING = 'PENDING', _('Queued')      # push queued to Celery
        SENT = 'SENT', _('Sent to Expo')      # accepted by Expo push service
        SKIPPED = 'SKIPPED', _('Skipped')     # blocked by prefs / quiet hours
        FAILED = 'FAILED', _('Failed')        # delivery error

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
    # Optional promo/coupon code the app can prefill at checkout.
    promo_code = models.CharField(max_length=50, blank=True, default='')
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

    # Campaign this notification belongs to (null for transactional/system ones).
    campaign = models.ForeignKey(
        'marketplace.NotificationCampaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )

    # --- Delivery / engagement analytics ---
    push_status = models.CharField(
        max_length=10, choices=PushStatus.choices, default=PushStatus.NONE, db_index=True
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    # Set when this notification is credited with a downstream order.
    converted_at = models.DateTimeField(null=True, blank=True)
    conversion_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

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

    def mark_opened(self):
        """Record the first time the user opened/viewed this notification.
        Idempotent — only the first open is timestamped. Rolls up to the
        owning campaign's opened_count."""
        if self.opened_at is not None:
            return False
        self.opened_at = timezone.now()
        fields = ['opened_at']
        if not self.is_read:
            self.is_read = True
            self.read_at = self.opened_at
            fields += ['is_read', 'read_at']
        self.save(update_fields=fields)
        if self.campaign_id:
            from django.db.models import F
            NotificationCampaign.objects.filter(pk=self.campaign_id).update(
                opened_count=F('opened_count') + 1
            )
        return True

    def mark_clicked(self):
        """Record a tap that routed the user into the app. Idempotent."""
        first_open = self.mark_opened()
        if self.clicked_at is not None:
            return False
        self.clicked_at = timezone.now()
        self.save(update_fields=['clicked_at'])
        if self.campaign_id:
            from django.db.models import F
            NotificationCampaign.objects.filter(pk=self.campaign_id).update(
                clicked_count=F('clicked_count') + 1
            )
        return True


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


class NotificationPreference(models.Model):
    """Per-user notification controls.

    In-app notification records are ALWAYS persisted (so the user keeps a full
    history in the bell feed). These toggles only gate *push* delivery, plus a
    quiet-hours window during which non-urgent pushes are withheld. Marketing /
    re-engagement campaigns additionally require `campaigns` (and, for PROMO,
    `promotions`) to be opted in.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preference',
        primary_key=True,
    )

    # Master switch — when False, no push is sent for any category.
    push_enabled = models.BooleanField(default=True)

    # Category-level push toggles.
    order_updates = models.BooleanField(default=True)
    payment_updates = models.BooleanField(default=True)
    promotions = models.BooleanField(default=True)
    # Re-engagement / marketing campaigns (inactivity win-backs, broadcasts).
    campaigns = models.BooleanField(default=True)
    # Referral programme nudges (invite friends, referral rewards).
    referrals = models.BooleanField(default=True)
    # Weekly tips / digest ("your laundry is in motion", care tips).
    weekly_tips = models.BooleanField(default=True)

    # Quiet hours (local 24h clock). When start == end or either is null the
    # window is disabled. Urgent-priority notifications ignore quiet hours.
    quiet_hours_start = models.PositiveSmallIntegerField(null=True, blank=True)
    quiet_hours_end = models.PositiveSmallIntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Notification Preference')
        verbose_name_plural = _('Notification Preferences')

    def __str__(self):
        return f"Notification preferences for {self.user_id}"

    # Map of fine-grained category/type → the toggle that governs its push.
    _CATEGORY_TOGGLES = {
        'ORDER': 'order_updates',
        'PAYMENT': 'payment_updates',
        'PAYMENT_SUCCESS': 'payment_updates',
        'PAYMENT_FAILED': 'payment_updates',
        'PROMO': 'promotions',
        'CAMPAIGN': 'campaigns',
        'REFERRAL': 'referrals',
        'WEEKLY_TIP': 'weekly_tips',
        'WEEKLY_TIPS': 'weekly_tips',
    }

    def allows_push(self, *, type='', category='') -> bool:
        """Whether a push for this type/category is permitted by the toggles."""
        if not self.push_enabled:
            return False
        toggle = (
            self._CATEGORY_TOGGLES.get((category or '').upper())
            or self._CATEGORY_TOGGLES.get((type or '').upper())
        )
        if toggle is None:
            return True  # uncategorised system messages are allowed by default
        return bool(getattr(self, toggle, True))

    def in_quiet_hours(self, hour: int) -> bool:
        """True if `hour` (0-23) falls inside the configured quiet window."""
        start, end = self.quiet_hours_start, self.quiet_hours_end
        if start is None or end is None or start == end:
            return False
        if start < end:
            return start <= hour < end
        # Window wraps midnight, e.g. 22 → 7.
        return hour >= start or hour < end


class NotificationCampaign(models.Model):
    """A Duolingo-style re-engagement / marketing campaign.

    A campaign targets a user *segment*, renders a title/body template, and is
    delivered as both an in-app notification and (preference-permitting) a push.
    Scheduled campaigns are driven by Celery beat tasks (see
    marketplace/tasks.py); ad-hoc campaigns can be sent from the admin.
    """

    class Segment(models.TextChoices):
        ALL = 'ALL', _('All users')
        ACTIVE = 'ACTIVE', _('Active users')
        INACTIVE = 'INACTIVE', _('Inactive users')
        PENDING_ORDERS = 'PENDING_ORDERS', _('Users with pending orders')
        UNPAID = 'UNPAID', _('Users with unpaid orders')
        PROMO_OPT_IN = 'PROMO_OPT_IN', _('Users opted into promotions')
        ABANDONED_BOOKING = 'ABANDONED_BOOKING', _('Users with abandoned bookings')
        REFERRAL = 'REFERRAL', _('Users who referred others')
        NEVER_ORDERED = 'NEVER_ORDERED', _('Users who never ordered')
        CITY = 'CITY', _('Users in a city')
        CUSTOM = 'CUSTOM', _('Custom (explicit user ids)')

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        SCHEDULED = 'SCHEDULED', _('Scheduled')
        SENDING = 'SENDING', _('Sending')
        SENT = 'SENT', _('Sent')
        FAILED = 'FAILED', _('Failed')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    segment = models.CharField(max_length=30, choices=Segment.choices, default=Segment.ALL)
    # Extra segment parameters, e.g. {"inactive_days": 14, "city": "Kumasi"}.
    segment_params = models.JSONField(default=dict, blank=True)

    title = models.CharField(max_length=255)
    body = models.TextField()
    image_url = models.URLField(blank=True, default='')
    action_url = models.CharField(max_length=500, blank=True, default='')
    # Optional promo/coupon code surfaced to the app (deep-link can prefill it).
    promo_code = models.CharField(max_length=50, blank=True, default='')
    notification_type = models.CharField(
        max_length=20, choices=Notification.Type.choices, default=Notification.Type.PROMO
    )
    # Governs which preference toggle gates this campaign's push.
    category = models.CharField(max_length=40, default='CAMPAIGN')
    priority = models.CharField(
        max_length=10, choices=Notification.Priority.choices, default=Notification.Priority.NORMAL
    )

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    scheduled_for = models.DateTimeField(null=True, blank=True, db_index=True)
    # After this instant the campaign is no longer eligible to send.
    expires_at = models.DateTimeField(null=True, blank=True)

    # Analytics counters.
    recipients_count = models.PositiveIntegerField(default=0)
    delivered_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    opened_count = models.PositiveIntegerField(default=0)
    clicked_count = models.PositiveIntegerField(default=0)
    # Conversions = recipients who placed/paid an order within the attribution
    # window after receiving this campaign. revenue_generated sums those orders.
    converted_count = models.PositiveIntegerField(default=0)
    revenue_generated = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_campaigns',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Notification Campaign')
        verbose_name_plural = _('Notification Campaigns')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} [{self.segment}] ({self.status})"

    @staticmethod
    def _rate(numerator, denominator):
        if not denominator:
            return 0.0
        return round((numerator / denominator) * 100, 2)

    @property
    def delivery_rate(self):
        """Delivered ÷ recipients (%)."""
        return self._rate(self.delivered_count, self.recipients_count)

    @property
    def open_rate(self):
        """Opened ÷ delivered (%)."""
        return self._rate(self.opened_count, self.delivered_count)

    @property
    def click_rate(self):
        """Clicked ÷ delivered (%)."""
        return self._rate(self.clicked_count, self.delivered_count)

    @property
    def failure_rate(self):
        """Failed ÷ recipients (%)."""
        return self._rate(self.failed_count, self.recipients_count)

    @property
    def conversion_rate(self):
        """Converted ÷ delivered (%)."""
        return self._rate(self.converted_count, self.delivered_count)

    @property
    def is_expired(self):
        return self.expires_at is not None and timezone.now() > self.expires_at
