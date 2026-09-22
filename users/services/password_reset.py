"""Password reset shared by the API (mobile app) and the hosted reset page."""
from django.conf import settings
from django.utils import timezone

from users.models import PasswordResetToken
from users.services.session_service import revoke_all_sessions_for_user


def build_reset_link(request, token_record):
    """Absolute link to the reset page this backend serves.

    The link used to point at FRONTEND_URL, a domain that did not exist in
    production, so every emailed link was dead. The backend hosts the page
    itself unless PASSWORD_RESET_PAGE_URL deliberately points elsewhere.
    """
    base = getattr(settings, 'PASSWORD_RESET_PAGE_URL', '') or request.build_absolute_uri('/reset-password/')
    return f"{base}?resetId={token_record.id}"


def find_valid_token(reset_id, raw_token):
    token_hash = PasswordResetToken._hash_token(raw_token.strip())
    lookup = {'token_hash': token_hash}
    if reset_id:
        lookup['id'] = reset_id
    record = PasswordResetToken.objects.filter(**lookup).select_related('user').first()
    return record if record and record.is_valid() else None


def apply_password_reset(token_record, new_password):
    """Set the password, spend the token, sign out every session, notify."""
    from marketplace.services.customer_events import notify_customer_event

    user = token_record.user
    user.set_password(new_password)
    user.save(update_fields=['password'])
    token_record.used_at = timezone.now()
    token_record.save(update_fields=['used_at'])
    revoke_all_sessions_for_user(user, reason='password_reset')
    notify_customer_event(user, 'PASSWORD_CHANGED', dedup_key=f'password_changed:{token_record.id}')
    return user
