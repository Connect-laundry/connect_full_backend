import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from marketplace.models import AuditLog
from marketplace.services.audit import record_audit
from users.models import User
from users.services.session_service import revoke_all_sessions_for_user

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountDeletionResult:
    user: User
    deactivated_at: object


def anonymize_local_account(
    user: User,
    *,
    reason: str,
    request=None,
) -> AccountDeletionResult:
    """Remove login/profile data while retaining anonymized transaction history."""
    avatar = None
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        avatar = locked_user.avatar if locked_user.avatar else None
        now = timezone.now()
        tombstone_suffix = str(locked_user.id).replace('-', '')[:12]

        revoke_all_sessions_for_user(locked_user, reason='account_deleted')
        locked_user.addresses.all().delete()
        locked_user.password_reset_tokens.all().delete()
        locked_user.push_devices.all().delete()

        locked_user.first_name = ''
        locked_user.last_name = ''
        locked_user.avatar = None
        locked_user.email = f'deleted-{tombstone_suffix}@deleted.simame'
        locked_user.phone = f'deleted-{tombstone_suffix}'
        locked_user.clerk_user_id = None
        locked_user.auth_provider = ''
        locked_user.primary_email = ''
        locked_user.social_provider = ''
        locked_user.social_profile_image_url = ''
        locked_user.email_verified = False
        locked_user.phone_verified = False
        locked_user.last_social_login_at = None
        locked_user.last_clerk_sign_in_at = None
        locked_user.clerk_created_at = None
        locked_user.clerk_updated_at = None
        locked_user.clerk_status = 'deleted'
        locked_user.clerk_metadata = {}
        locked_user.referral_code = None
        locked_user.referred_by = None
        locked_user.is_verified = False
        locked_user.is_active = False
        locked_user.last_clerk_sync = now
        locked_user.deactivated_at = now
        locked_user.deactivation_reason = reason
        locked_user.set_unusable_password()
        locked_user.save(update_fields=[
            'first_name', 'last_name', 'avatar', 'email', 'phone',
            'clerk_user_id', 'auth_provider', 'primary_email', 'social_provider',
            'social_profile_image_url', 'email_verified', 'phone_verified',
            'last_social_login_at', 'last_clerk_sign_in_at', 'last_clerk_sync',
            'clerk_created_at', 'clerk_updated_at', 'clerk_status',
            'clerk_metadata', 'referral_code', 'referred_by', 'is_verified',
            'is_active', 'deactivated_at', 'deactivation_reason', 'password',
            'updated_at',
        ])

    if avatar:
        try:
            avatar.delete(save=False)
        except Exception:
            logger.warning(
                'Deleted account avatar could not be removed from storage',
                extra={'user_id': str(user.pk)},
            )

    record_audit(
        action=AuditLog.Action.SECURITY_EVENT,
        actor=locked_user,
        request=request,
        target_type='User',
        target_id=locked_user.id,
        target_repr=locked_user.email,
        metadata={'event': 'account_deleted', 'reason': reason},
    )
    return AccountDeletionResult(user=locked_user, deactivated_at=locked_user.deactivated_at)
