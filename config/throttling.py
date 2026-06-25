# pyre-ignore[missing-module]
from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle


def _normalized_account_value(request):
    email = str(request.data.get('email', '') or '').strip().lower()
    if email:
        return email
    phone = str(request.data.get('phone', '') or '').strip()
    if phone:
        return phone
    token = str(request.data.get('token', '') or '').strip()
    if token:
        return token[:64]
    return ''


class BurstUserThrottle(UserRateThrottle):
    scope = 'burst_user'


class SustainedUserThrottle(UserRateThrottle):
    scope = 'sustained_user'


class ReviewThrottle(SimpleRateThrottle):
    scope = 'review'

    def get_cache_key(self, request, view):
        ident = request.user.id if request.user.is_authenticated else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class FeedbackThrottle(SimpleRateThrottle):
    scope = 'feedback'

    def get_cache_key(self, request, view):
        ident = request.user.id if request.user.is_authenticated else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class LegalPublicThrottle(AnonRateThrottle):
    scope = 'legal_public'


class LoginIPThrottle(AnonRateThrottle):
    scope = 'auth_login_ip'


class RegisterIPThrottle(AnonRateThrottle):
    scope = 'auth_register_ip'


class RefreshIPThrottle(AnonRateThrottle):
    scope = 'auth_refresh_ip'


class PasswordResetIPThrottle(AnonRateThrottle):
    scope = 'password_reset_ip'


class ResetPasswordIPThrottle(AnonRateThrottle):
    scope = 'reset_password_ip'


class AccountScopedThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        account_value = _normalized_account_value(request)
        if not account_value:
            account_value = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': account_value}


class LoginAccountThrottle(AccountScopedThrottle):
    scope = 'auth_login_account'


class RegisterAccountThrottle(AccountScopedThrottle):
    scope = 'auth_register_account'


class PasswordResetAccountThrottle(AccountScopedThrottle):
    scope = 'password_reset_account'


class PaymentCreateThrottle(SimpleRateThrottle):
    scope = 'payment_create'

    def get_cache_key(self, request, view):
        ident = request.user.id if request.user.is_authenticated else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class AdminSearchThrottle(UserRateThrottle):
    scope = 'admin_search'


class NotifTrackThrottle(SimpleRateThrottle):
    """Per-user limit on notification open/click tracking events."""
    scope = 'notif_track'

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}
