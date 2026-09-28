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

    ACTIVE_STATUSES = (Status.PENDING, Status.SENDING, Status.RETRY)
    ACCEPTED_STATUSES = (Status.SUBMITTED, Status.SENT, Status.DELIVERED)
    RETRYABLE_BY_ADMIN = (Status.FAILED, Status.DEAD, Status.UNKNOWN, Status.UNDELIVERED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(AdminNotificationEvent, on_delete=models.CASCADE, related_name='deliveries')
    channel = models.CharField(max_length=16, choices=Channel.choices)
    # SMS/WhatsApp: normalized 233XXXXXXXXX. Telegram: numeric chat id.
    recipient = models.CharField(max_length=20)
    provider = models.CharField(max_length=32)
    # "<event uuid>:<channel>:<recipient>"
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
        indexes = [models.Index(fields=['status', 'next_attempt_at'])]
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'channel', 'recipient'], name='uniq_admin_notif_delivery_recipient',
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
