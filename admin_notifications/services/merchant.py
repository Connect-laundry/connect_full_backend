"""Laundry-owner booking SMS: when an owner is told, and at which number.

Owner alerts ride the same outbox, dispatcher, Arkesel provider and delivery
reports as the operations alerts. This module only adds the merchant rules:

When (``template_for``)
- A booking is real once the customer is committed to it:
  * pay-first orders (Paystack card/MoMo/bank transfer, priced by item or by
    weight) when the payment is verified - PAYMENT_CONFIRMED. The order was
    created earlier as an unpaid draft, and many drafts are abandoned at the
    payment screen; telling the laundry about those would send riders to
    customers who never booked.
  * cash on delivery, and pay-after-quote requests, at creation - NEW_ORDER.
    No online payment precedes them and the laundry must act (collect, quote).
- Cancellations and schedule/address changes go to an owner only if that
  owner was already told about the booking.
- Rejections and price finalization are done by the laundry itself: no SMS.

Who (``resolve_recipient``)
- Only the owner of ``order.laundry``, at the number in that laundry's
  MerchantNotificationSettings, and only once it is verified. There is no
  fallback to any other number, laundry or account. A missing or unverified
  number blocks the alert (visible to ops and the owner); the booking is
  unaffected.
- The same checks run again just before sending, so a number changed or
  alerts switched off after the booking are honoured.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .. import conf
from ..models import AdminNotificationDelivery as Delivery
from ..models import AdminNotificationEvent as Event
from ..models import MerchantNotificationSettings, MerchantSmsSendLog
from ..phone import AdminRecipientError, normalize_admin_recipient

BOOKING = 'merchant_new_booking_v1'
CANCELLED = 'merchant_booking_cancelled_v1'
CHANGED = 'merchant_booking_changed_v1'
TEMPLATE_LABELS = {BOOKING: 'New booking', CANCELLED: 'Booking cancelled', CHANGED: 'Booking updated'}

# Why an owner alert was not sent (stored in last_error_class).
NO_VERIFIED_PHONE = 'NO_VERIFIED_PHONE'
ALERTS_OFF = 'OWNER_ALERTS_OFF'
NO_OWNER = 'NO_LAUNDRY_OWNER'
OWNER_INACTIVE = 'OWNER_INACTIVE'
PHONE_CHANGED = 'PHONE_CHANGED'
LAUNDRY_CHANGED = 'LAUNDRY_CHANGED'
ORDER_CLOSED = 'ORDER_CLOSED'
BOOKING_NOT_SENT = 'BOOKING_ALERT_NOT_SENT'
FEATURE_OFF = 'MERCHANT_SMS_OFF'
NOT_IN_PILOT = 'NOT_IN_PILOT'

BLOCK_MESSAGES = {
    NO_VERIFIED_PHONE: 'No verified SMS number for this laundry. Add one in Seller Center settings.',
    ALERTS_OFF: 'The owner turned booking SMS alerts off.',
    NO_OWNER: 'The laundry has no owner account.',
    OWNER_INACTIVE: 'The owner account is inactive.',
    PHONE_CHANGED: 'The alert number changed before sending; not sent to the old number.',
    LAUNDRY_CHANGED: 'The order now belongs to a different laundry.',
    ORDER_CLOSED: 'The order was cancelled or rejected before the alert went out.',
    BOOKING_NOT_SENT: 'The owner was never told about this booking, so no follow-up was sent.',
    FEATURE_OFF: 'Owner SMS alerts are switched off for SIMAME.',
    NOT_IN_PILOT: 'This laundry is not in the SMS alert rollout yet.',
}

CLOSED_ORDER_STATUSES = {'CANCELLED', 'REJECTED'}


def is_pay_first(order) -> bool:
    """The customer pays online before the booking counts (see module doc)."""
    return order.payment_method != 'CASH' and order.pricing_mode != 'CUSTOM_QUOTE'


def template_for(event_type: str, order) -> str | None:
    E = Event.EventType
    if event_type == E.NEW_ORDER:
        return None if is_pay_first(order) else BOOKING
    if event_type == E.PAYMENT_CONFIRMED:
        return BOOKING if is_pay_first(order) else None
    if event_type == E.ORDER_CANCELLED:
        return CANCELLED
    if event_type in (E.ORDER_RESCHEDULED, E.PICKUP_LOCATION_CHANGED, E.DELIVERY_LOCATION_CHANGED):
        return CHANGED
    return None


def _owner_was_told(order_id) -> bool:
    return Delivery.objects.filter(
        event__order_id=order_id, audience=Delivery.Audience.MERCHANT, template=BOOKING,
    ).exclude(status__in=[Delivery.Status.BLOCKED, Delivery.Status.FAILED, Delivery.Status.DEAD]).exists()


def resolve_recipient(laundry) -> tuple[str, str]:
    """(normalized number, '') or ('', block reason). Never another laundry's number."""
    if not conf.merchant_sms_enabled():
        return '', FEATURE_OFF
    if not conf.merchant_sms_allowed_for(laundry.pk):
        return '', NOT_IN_PILOT
    if not laundry.owner_id:
        return '', NO_OWNER
    if not getattr(laundry.owner, 'is_active', False):
        return '', OWNER_INACTIVE
    row = MerchantNotificationSettings.objects.filter(laundry_id=laundry.pk).first()
    if row is None or not row.is_verified:
        return '', NO_VERIFIED_PHONE
    if not row.sms_enabled:
        return '', ALERTS_OFF
    return row.phone, ''


def merchant_delivery_for(event, order) -> Delivery | None:
    """The owner alert row for ``event``, or None when the owner is not told.

    Runs inside the outbox transaction. Idempotent through the delivery's
    unique dedup key (one owner alert per event and laundry).
    """
    if not conf.merchant_sms_enabled():
        return None
    template = template_for(event.event_type, order)
    if template is None:
        return None
    if template != BOOKING and not _owner_was_told(order.pk):
        return None
    laundry = order.laundry
    if not conf.merchant_sms_allowed_for(laundry.pk):
        return None  # outside the rollout: nothing to show anyone
    recipient, reason = resolve_recipient(laundry)
    row = Delivery(
        event=event, audience=Delivery.Audience.MERCHANT, laundry=laundry, template=template,
        channel=Delivery.Channel.SMS, provider='arkesel_sms', recipient=recipient,
        dedup_key=f'{event.pk}:{Delivery.Audience.MERCHANT}:{laundry.pk}',
    )
    if reason:
        row.status = Delivery.Status.BLOCKED
        row.failed_at = timezone.now()
        row.last_error_class = reason
        row.last_error_safe_message = BLOCK_MESSAGES[reason]
    return row


def dispatch_block_reason(delivery) -> str:
    """Re-check an owner alert just before it is sent. '' means send it."""
    from ordering.models import Order

    order = Order.objects.select_related('laundry__owner').only(
        'id', 'status', 'laundry_id', 'laundry__owner_id', 'laundry__owner__is_active',
    ).get(pk=delivery.event.order_id)
    if order.laundry_id != delivery.laundry_id:
        return LAUNDRY_CHANGED
    recipient, reason = resolve_recipient(order.laundry)
    if reason:
        return reason
    if recipient != delivery.recipient:
        return PHONE_CHANGED
    if delivery.template == BOOKING and order.status in CLOSED_ORDER_STATUSES:
        return ORDER_CLOSED
    if delivery.template != BOOKING and not Delivery.objects.filter(
        event__order_id=order.pk, audience=Delivery.Audience.MERCHANT, template=BOOKING,
        status__in=list(Delivery.ACCEPTED_STATUSES) + [Delivery.Status.UNKNOWN, Delivery.Status.UNDELIVERED],
    ).exists():
        return BOOKING_NOT_SENT
    return ''


# --- Number verification -------------------------------------------------------

OTP_MAX_ATTEMPTS = 5


class VerificationError(Exception):
    """Shown to the owner as-is; never contains a code or a full number."""

    def __init__(self, message, *, code='invalid', status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def settings_for(laundry) -> MerchantNotificationSettings:
    row, _ = MerchantNotificationSettings.objects.get_or_create(laundry=laundry)
    return row


def _otp_digest(laundry_id, phone: str, code: str) -> str:
    key = settings.SECRET_KEY.encode('utf-8')
    return hmac.new(key, f'merchant-sms-otp:{laundry_id}:{phone}:{code}'.encode('utf-8'), hashlib.sha256).hexdigest()


def _recent_sends(laundry, kind, window) -> int:
    return MerchantSmsSendLog.objects.filter(
        laundry=laundry, kind=kind, created_at__gte=timezone.now() - window,
    ).count()


def normalize_owner_number(raw) -> str:
    try:
        return normalize_admin_recipient(raw)
    except AdminRecipientError:
        raise VerificationError('Enter a valid Ghana mobile number, e.g. 024 123 4567.', code='invalid_phone')


def _send_now(number: str, text: str):
    from ..providers import get_sms_provider
    provider = get_sms_provider()
    if provider.missing_config():
        raise VerificationError('SMS is not available right now. Please try again later.',
                                code='sms_unavailable', status=503)
    return provider.send(number, text)


def start_verification(laundry, user, raw_phone) -> MerchantNotificationSettings:
    """Text a 6-digit code to ``raw_phone``. The current verified number keeps
    receiving alerts until the new one is confirmed."""
    number = normalize_owner_number(raw_phone)
    if _recent_sends(laundry, MerchantSmsSendLog.Kind.OTP, timedelta(hours=1)) >= conf.merchant_otp_hourly_limit():
        raise VerificationError('Too many codes requested. Try again in an hour.', code='throttled', status=429)
    code = f'{secrets.randbelow(10 ** 6):06d}'
    text = (f'{conf.message_prefix()}Your SIMAME code is {code}. It confirms this number for booking alerts '
            f'for your laundry. It expires in {conf.merchant_otp_ttl_minutes()} minutes. Do not share it.')
    result = _send_now(number, text)
    MerchantSmsSendLog.objects.create(laundry=laundry, kind=MerchantSmsSendLog.Kind.OTP, accepted=result.accepted)
    if not result.accepted:
        raise VerificationError('We could not send a code to that number. Check it and try again.',
                                code='send_failed', status=502)
    row = settings_for(laundry)
    row.pending_phone = number
    row.otp_hash = _otp_digest(laundry.pk, number, code)
    row.otp_expires_at = timezone.now() + timedelta(minutes=conf.merchant_otp_ttl_minutes())
    row.otp_attempts = 0
    row.updated_by = user
    row.save(update_fields=['pending_phone', 'otp_hash', 'otp_expires_at', 'otp_attempts', 'updated_by',
                            'updated_at'])
    return row


def confirm_verification(laundry, user, code) -> MerchantNotificationSettings:
    code = str(code or '').strip()
    with transaction.atomic():
        row = MerchantNotificationSettings.objects.select_for_update().filter(laundry=laundry).first()
        if row is None or not row.pending_phone or not row.otp_hash:
            raise VerificationError('Request a code first.', code='no_pending')
        if row.otp_expires_at is None or row.otp_expires_at < timezone.now():
            _clear_pending(row)
            raise VerificationError('That code has expired. Request a new one.', code='expired')
        if row.otp_attempts >= OTP_MAX_ATTEMPTS:
            _clear_pending(row)
            raise VerificationError('Too many wrong codes. Request a new one.', code='locked')
        row.otp_attempts += 1
        if not (code.isdigit() and len(code) == 6
                and hmac.compare_digest(_otp_digest(laundry.pk, row.pending_phone, code), row.otp_hash)):
            row.save(update_fields=['otp_attempts', 'updated_at'])
            raise VerificationError('That code is not correct.', code='wrong_code')
        row.phone = row.pending_phone
        row.phone_verified_at = timezone.now()
        row.verification_method = MerchantNotificationSettings.VerificationMethod.OTP
        row.updated_by = user
        _clear_pending(row, save=False)
        row.save()
    _audit(user, laundry, 'merchant_sms_phone_verified', method='OTP')
    return row


def _clear_pending(row, save=True):
    row.pending_phone = ''
    row.otp_hash = ''
    row.otp_expires_at = None
    row.otp_attempts = 0
    if save:
        row.save(update_fields=['pending_phone', 'otp_hash', 'otp_expires_at', 'otp_attempts', 'updated_at'])


def account_phone(user) -> str:
    """The owner's sign-in phone, normalized, only if Clerk verified it."""
    if not (getattr(user, 'phone', '') and getattr(user, 'phone_verified', False)):
        return ''
    try:
        return normalize_admin_recipient(user.phone)
    except AdminRecipientError:
        return ''


def adopt_account_phone(laundry, user) -> MerchantNotificationSettings:
    number = account_phone(user)
    if not number:
        raise VerificationError('Your sign-in phone is not verified. Verify a number by code instead.',
                                code='account_phone_unverified')
    row = settings_for(laundry)
    row.phone = number
    row.phone_verified_at = timezone.now()
    row.verification_method = MerchantNotificationSettings.VerificationMethod.ACCOUNT
    row.updated_by = user
    _clear_pending(row, save=False)
    row.save()
    _audit(user, laundry, 'merchant_sms_phone_verified', method='ACCOUNT')
    return row


def set_enabled(laundry, user, enabled: bool) -> MerchantNotificationSettings:
    row = settings_for(laundry)
    row.sms_enabled = bool(enabled)
    row.updated_by = user
    row.save(update_fields=['sms_enabled', 'updated_by', 'updated_at'])
    _audit(user, laundry, 'merchant_sms_alerts_toggled', enabled=bool(enabled))
    return row


def send_test(laundry, user) -> None:
    row = settings_for(laundry)
    if not row.is_verified:
        raise VerificationError('Verify a number first.', code='unverified')
    if _recent_sends(laundry, MerchantSmsSendLog.Kind.TEST, timedelta(days=1)) >= conf.merchant_test_daily_limit():
        raise VerificationError('Daily test limit reached. Try again tomorrow.', code='throttled', status=429)
    text = (f'{conf.message_prefix()}SIMAME TEST: booking alerts for your laundry reach this phone. '
            f'No action needed.')
    result = _send_now(row.phone, text)
    MerchantSmsSendLog.objects.create(laundry=laundry, kind=MerchantSmsSendLog.Kind.TEST, accepted=result.accepted)
    if not result.accepted:
        raise VerificationError('The test message could not be sent. Try again later.', code='send_failed',
                                status=502)
    _audit(user, laundry, 'merchant_sms_test_sent')


def _audit(user, laundry, event, **metadata):
    try:
        from marketplace.models import AuditLog
        from marketplace.services.audit import record_audit
        record_audit(action=AuditLog.Action.SECURITY_EVENT, actor=user, target_type='Laundry',
                     target_id=laundry.pk, target_repr=laundry.name, metadata={'event': event, **metadata})
    except Exception:  # auditing must not break the owner's settings change
        pass
