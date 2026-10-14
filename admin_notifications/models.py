"""Transactional outbox for operations alerts.

An ``AdminNotificationEvent`` is written in the same database transaction as
the order change it describes, so an alert exists if and only if the change
committed. Its ``payload`` is a frozen snapshot of the order at that moment:
messages are rendered from it, never from live catalogue or order rows.

Each event fans out into one ``AdminNotificationDelivery`` per channel and
recipient. Uniqueness is enforced by the database, not by Python checks, so
retried requests, repeated webhooks and racing workers cannot duplicate an
event or a delivery.
"""
import uuid

from django.db import models
from django.utils import timezone


class AdminNotificationEvent(models.Model):
    class EventType(models.TextChoices):
        NEW_ORDER = 'NEW_ORDER', 'New order'
        PAYMENT_CONFIRMED = 'PAYMENT_CONFIRMED', 'Payment confirmed'
        ORDER_CANCELLED = 'ORDER_CANCELLED', 'Order cancelled'
        ORDER_REJECTED = 'ORDER_REJECTED', 'Order rejected by laundry'
        ORDER_RESCHEDULED = 'ORDER_RESCHEDULED', 'Order rescheduled'
        PICKUP_LOCATION_CHANGED = 'PICKUP_LOCATION_CHANGED', 'Pickup location changed'
        DELIVERY_LOCATION_CHANGED = 'DELIVERY_LOCATION_CHANGED', 'Delivery location changed'
        ORDER_PRICE_FINALIZED = 'ORDER_PRICE_FINALIZED', 'Order price finalized'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        'ordering.Order', on_delete=models.CASCADE, related_name='admin_notification_events',
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    # e.g. "NEW_ORDER:<order id>". The unique index is the idempotency guard.
    idempotency_key = models.CharField(max_length=200, unique=True)
    payload = models.JSONField()
    payload_version = models.PositiveSmallIntegerField(default=1)
    environment = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Set once every delivery has reached a terminal state.
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['order', 'event_type'])]
        verbose_name = 'Admin notification event'

    def __str__(self):
        order_no = (self.payload or {}).get('order', {}).get('order_no', '')
        return f'{self.event_type} {order_no}'


class AdminNotificationDelivery(models.Model):
    class Channel(models.TextChoices):
        SMS = 'SMS', 'SMS'
        WHATSAPP = 'WHATSAPP', 'WhatsApp'
        TELEGRAM = 'TELEGRAM', 'Telegram'

    class Audience(models.TextChoices):
        # SIMAME operations staff (env-configured recipients).
        ADMIN = 'ADMIN', 'Operations'
        # The owner of the laundry fulfilling the order, at the verified
        # number in MerchantNotificationSettings. Never anyone else.
        MERCHANT = 'MERCHANT', 'Laundry owner'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENDING = 'SENDING', 'Sending'
        RETRY = 'RETRY', 'Waiting to retry'
        # Provider accepted the message. For SMS a delivery report may follow.
        SUBMITTED = 'SUBMITTED', 'Submitted to provider'
        SENT = 'SENT', 'Sent'
        DELIVERED = 'DELIVERED', 'Delivered to handset'
        UNDELIVERED = 'UNDELIVERED', 'Not delivered (provider report)'
        # The request may or may not have reached the provider (timeout after
        # sending, worker died mid-send). Not retried automatically, so a
        # lost response cannot turn into duplicate alerts.
        UNKNOWN = 'UNKNOWN', 'Unknown (possible duplicate risk)'
        FAILED = 'FAILED', 'Failed (needs config/provider fix)'
        DEAD = 'DEAD', 'Dead (retries exhausted or expired)'
        # Never sent on purpose: the laundry has no verified SMS number, turned
        # alerts off, or is outside the rollout. The reason is in last_error_*.
        BLOCKED = 'BLOCKED', 'Blocked (not sent by policy)'

    ACTIVE_STATUSES = (Status.PENDING, Status.SENDING, Status.RETRY)
    ACCEPTED_STATUSES = (Status.SUBMITTED, Status.SENT, Status.DELIVERED)
    RETRYABLE_BY_ADMIN = (Status.FAILED, Status.DEAD, Status.UNKNOWN, Status.UNDELIVERED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(AdminNotificationEvent, on_delete=models.CASCADE, related_name='deliveries')
    channel = models.CharField(max_length=16, choices=Channel.choices)
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.ADMIN, db_index=True)
    # MERCHANT deliveries only: the laundry whose owner this goes to. Lets an
    # owner list their own alerts without reading any other laundry's rows.
    laundry = models.ForeignKey(
        'laundries.Laundry', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sms_notification_deliveries',
    )
    # Which message template rendered this delivery ('' = the admin default).
    template = models.CharField(max_length=40, blank=True, default='')
    # SMS/WhatsApp: normalized 233XXXXXXXXX. Telegram: numeric chat id.
    # Empty for a BLOCKED merchant delivery that had no verified number.
    recipient = models.CharField(max_length=20, blank=True)
    provider = models.CharField(max_length=32)
    # "<event uuid>:<channel>:<recipient>" (admin) or
    # "<event uuid>:MERCHANT:<laundry uuid>" (one owner alert per event).
    dedup_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)

    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True)

    # Long alerts go out as several labelled parts; a retry resumes after
    # the last part the provider accepted instead of repeating it.
    parts_total = models.PositiveSmallIntegerField(default=0)
    parts_sent = models.PositiveSmallIntegerField(default=0)
    estimated_segments = models.PositiveIntegerField(default=0)

    provider_message_id = models.CharField(max_length=128, blank=True, default='')
    last_error_class = models.CharField(max_length=40, blank=True, default='')
    last_error_safe_message = models.CharField(max_length=300, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'next_attempt_at']),
            models.Index(fields=['laundry', '-created_at'], name='admin_notif_laundry_recent'),
        ]
        constraints = [
            # An ops number that is also an owner's number gets both messages:
            # they are different alerts, so audience is part of the key.
            models.UniqueConstraint(
                fields=['event', 'channel', 'recipient', 'audience'], name='uniq_admin_notif_delivery_recipient',
            ),
        ]
        verbose_name = 'Admin notification delivery'
        verbose_name_plural = 'Admin notification deliveries'

    def __str__(self):
        from .phone import mask_recipient
        return f'{self.channel} {mask_recipient(self.recipient)} {self.status}'


class AdminNotificationProviderMessage(models.Model):
    """One provider-accepted message (one part of a delivery).

    SMS delivery reports arrive per ``sms_id``; this table is where a callback
    finds exactly which delivery it belongs to.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery = models.ForeignKey(
        AdminNotificationDelivery, on_delete=models.CASCADE, related_name='provider_messages',
    )
    part_index = models.PositiveSmallIntegerField()
    provider = models.CharField(max_length=32)
    # Null when the provider accepted the message without returning an id.
    provider_message_id = models.CharField(max_length=128, null=True, blank=True)
    provider_status = models.CharField(max_length=24, blank=True, default='')
    estimated_segments = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    status_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['part_index']
        constraints = [
            models.UniqueConstraint(fields=['delivery', 'part_index'], name='uniq_admin_notif_part'),
            models.UniqueConstraint(
                fields=['provider', 'provider_message_id'], name='uniq_admin_notif_provider_msg',
            ),
        ]


class MerchantNotificationSettings(models.Model):
    """Where a laundry's booking SMS alerts go, and whether that number is proven.

    One row per laundry. ``phone`` is only ever sent customer booking details
    once ``phone_verified_at`` is set, by a one-time code sent to that exact
    number, or by adopting the owner's sign-in phone that Clerk has already
    verified. Changing the number clears the verification.
    """
    class VerificationMethod(models.TextChoices):
        OTP = 'OTP', 'One-time code'
        ACCOUNT = 'ACCOUNT', 'Verified sign-in phone'

    laundry = models.OneToOneField(
        'laundries.Laundry', on_delete=models.CASCADE, related_name='sms_notification_settings',
    )
    sms_enabled = models.BooleanField(default=True)
    # Normalized 233XXXXXXXXX, or '' when none has been set.
    phone = models.CharField(max_length=20, blank=True, default='')
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    verification_method = models.CharField(max_length=10, choices=VerificationMethod.choices, blank=True, default='')

    # Pending one-time code for ``pending_phone``. Only a salted hash is kept.
    pending_phone = models.CharField(max_length=20, blank=True, default='')
    otp_hash = models.CharField(max_length=128, blank=True, default='')
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)

    updated_by = models.ForeignKey(
        'users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Laundry SMS alert settings'
        verbose_name_plural = 'Laundry SMS alert settings'

    @property
    def is_verified(self) -> bool:
        return bool(self.phone and self.phone_verified_at)

    def __str__(self):
        from .phone import mask_recipient
        state = 'verified' if self.is_verified else 'unverified'
        return f'{self.laundry_id} {mask_recipient(self.phone) if self.phone else "no phone"} ({state})'


class MerchantSmsSendLog(models.Model):
    """Owner-initiated sends (verification codes, test messages).

    Counted per laundry to rate-limit them in the database, which every web
    process shares; production has no shared cache. Holds no number or text.
    """
    class Kind(models.TextChoices):
        OTP = 'OTP', 'Verification code'
        TEST = 'TEST', 'Test message'

    laundry = models.ForeignKey('laundries.Laundry', on_delete=models.CASCADE, related_name='+')
    kind = models.CharField(max_length=8, choices=Kind.choices)
    accepted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['laundry', 'kind', 'created_at'], name='merchant_sms_log_window')]
