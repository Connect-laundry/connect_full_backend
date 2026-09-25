"""
Service for managing laundry payout recipients and account destinations.

High-security surface:
- Validates ownership and explicit authorization.
- Normalizes Ghanaian phone numbers strictly.
- Never sends Paystack keys to frontend.
- Ensures idempotency (safely reuses existing recipients).
- Tolerates Paystack outages without losing laundry registration.
"""

import logging
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from config.redaction import mask_reference
from laundries.models.laundry import Laundry, OwnerAuditLog
from users.utils.phone import (
    PhoneValidationError,
    mask_phone_number,
    normalize_phone,
    to_ghana_momo_account_number,
)
from .paystack import PaystackService

logger = logging.getLogger(__name__)

SUPPORTED_GHANA_MOMO_CODES = {'MTN', 'VOD', 'ATL'}


class RecipientService:

    @classmethod
    def setup_payout_account(
        cls,
        laundry: Laundry,
        user,
        payout_method: str = 'MOBILE_MONEY',
        payout_provider: str = 'MTN',
        payout_phone: str = '',
        account_name: str = '',
        confirmed: bool = False,
        paystack_client=None,
    ) -> dict:
        """
        Validate, normalize, and register an owner's payout destination.

        Raises ValueError on validation failure.
        Returns a dict with payout status and masked account details.
        """
        if not (user.is_staff or getattr(laundry, 'owner_id', None) == user.id):
            raise PermissionDenied("You are not authorised to modify this laundry's payout account.")

        if not confirmed:
            raise ValueError(
                "You must explicitly confirm that this payout account belongs to you "
                "or your business and that you are authorised to receive funds through it."
            )

        payout_method = (payout_method or 'MOBILE_MONEY').upper()
        if payout_method not in (Laundry.PayoutMethod.MOBILE_MONEY, Laundry.PayoutMethod.BANK_ACCOUNT):
            payout_method = Laundry.PayoutMethod.MOBILE_MONEY

        if payout_method == Laundry.PayoutMethod.BANK_ACCOUNT:
            raise ValueError("Bank account payouts are not available yet. Please use Mobile Money.")

        provider = (payout_provider or '').strip().upper()
        if payout_method == Laundry.PayoutMethod.MOBILE_MONEY and not provider:
            raise ValueError("Please select a Mobile Money network (MTN, Telecel, or ATMoney).")
        if payout_method == Laundry.PayoutMethod.MOBILE_MONEY and provider not in SUPPORTED_GHANA_MOMO_CODES:
            raise ValueError("Please choose MTN, Telecel, or ATMoney.")

        raw_phone = (payout_phone or getattr(laundry, 'phone_number', '') or '').strip()
        if not raw_phone:
            raise ValueError("A valid payout phone number is required.")

        try:
            account_number = to_ghana_momo_account_number(raw_phone)
            e164 = normalize_phone(raw_phone)
        except PhoneValidationError as exc:
            raise ValueError(str(exc))

        masked = mask_phone_number(e164)
        recipient_name = (
            account_name
            or getattr(laundry, 'name', '')
            or (user.get_full_name() if hasattr(user, 'get_full_name') else '')
            or 'Simame Merchant'
        ).strip()

        # Idempotency: If already configured with identical details and READY, reuse.
        if (
            laundry.payout_status == Laundry.PayoutStatus.PAYOUT_READY
            and laundry.paystack_recipient_code
            and laundry.payout_provider == provider
            and laundry.payout_phone_normalized == e164
            and laundry.payout_method == payout_method
        ):
            logger.info(
                "Payout recipient already configured; reusing existing code",
                extra={"laundry_id": str(laundry.id), "provider": provider},
            )
            return {
                'status': True,
                'reused': True,
                'payout_status': laundry.payout_status,
                'recipient_code': laundry.paystack_recipient_code,
                'payout_provider': laundry.payout_provider,
                'payout_phone_normalized': laundry.payout_phone_normalized,
                'masked_account': masked,
                'message': 'Payout account is verified and ready.',
            }

        was_ready = bool(
            laundry.payout_status == Laundry.PayoutStatus.PAYOUT_READY
            and laundry.paystack_recipient_code
        )

        client = paystack_client or PaystackService()
        res = client.create_transfer_recipient(
            name=recipient_name,
            account_number=account_number,
            bank_code=provider,
            recipient_type='mobile_money',
            currency='GHS',
        )

        is_success = bool(res.get('status')) and bool(res.get('data', {}).get('recipient_code'))

        with transaction.atomic():
            locked = Laundry.objects.select_for_update().get(pk=laundry.pk)
            locked.payout_method = payout_method
            locked.payout_provider = provider
            locked.payout_phone = raw_phone
            locked.payout_phone_normalized = e164
            locked.payout_account_name = recipient_name
            locked.payout_confirmed_at = timezone.now()
            locked.payout_confirmed_by = user

            if is_success:
                recipient_code = res['data']['recipient_code']
                locked.paystack_recipient_code = recipient_code
                locked.payout_status = Laundry.PayoutStatus.PAYOUT_READY
                locked.recipient_created_at = timezone.now()
                locked.payout_failure_reason = ''
                locked.save(
                    update_fields=[
                        'paystack_recipient_code',
                        'payout_method',
                        'payout_provider',
                        'payout_phone',
                        'payout_phone_normalized',
                        'payout_account_name',
                        'payout_status',
                        'payout_confirmed_at',
                        'payout_confirmed_by',
                        'recipient_created_at',
                        'payout_failure_reason',
                        'updated_at',
                    ]
                )

                OwnerAuditLog.objects.create(
                    laundry=locked,
                    actor=user,
                    action='PAYOUT_RECIPIENT_CONFIGURED',
                    details={
                        'recipient_code': mask_reference(recipient_code),
                        'provider': provider,
                        'masked_phone': masked,
                    },
                )
                logger.info(
                    "Payout recipient registered successfully",
                    extra={"laundry_id": str(locked.id), "provider": provider},
                )
                from . import payout_notifications
                transaction.on_commit(
                    lambda: payout_notifications.payout_account_ready(locked, changed=was_ready)
                )
                return {
                    'status': True,
                    'reused': False,
                    'payout_status': locked.payout_status,
                    'recipient_code': recipient_code,
                    'payout_provider': provider,
                    'payout_phone_normalized': e164,
                    'masked_account': masked,
                    'message': 'Payout account verified and ready.',
                }

            # Paystack error or network timeout: Do NOT crash onboarding.
            error_msg = res.get('message') or 'Unable to register transfer recipient with payment provider.'
            locked.payout_status = Laundry.PayoutStatus.PAYOUT_FAILED_RETRYABLE
            locked.payout_failure_reason = str(error_msg)[:500]
            locked.save(
                update_fields=[
                    'payout_method',
                    'payout_provider',
                    'payout_phone',
                    'payout_phone_normalized',
                    'payout_account_name',
                    'payout_status',
                    'payout_confirmed_at',
                    'payout_confirmed_by',
                    'payout_failure_reason',
                    'updated_at',
                ]
            )

            OwnerAuditLog.objects.create(
                laundry=locked,
                actor=user,
                action='PAYOUT_RECIPIENT_FAILED',
                details={'provider': provider, 'error': str(error_msg)[:200]},
            )
            logger.warning(
                "Payout recipient creation failed; set to retryable state",
                extra={"laundry_id": str(locked.id), "error": str(error_msg)},
            )
            from . import payout_notifications
            transaction.on_commit(lambda: payout_notifications.payout_account_needs_attention(locked))
            return {
                'status': False,
                'retryable': True,
                'payout_status': locked.payout_status,
                'message': error_msg,
                'payout_provider': provider,
                'payout_phone_normalized': e164,
                'masked_account': masked,
            }
