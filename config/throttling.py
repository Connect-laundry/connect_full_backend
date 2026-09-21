"""Rate limits designed for shared networks.

Principles (see SIMAME_RATE_LIMIT_AND_LAUNCH_GUARD_AUDIT_2026-09-21.md):

* Campus Wi-Fi, hostels and carrier-grade NAT put many legitimate customers
  behind one public IP. IP limits are therefore generous and short-windowed:
  they stop floods, not normal launch traffic.
* The tight limits key on something one person controls: the email being
  signed up / reset, the refresh token, the authenticated user.
* Client identity comes from config.client_ip (Cloudflare's True-Client-IP on
  Render). A client cannot forge it by sending X-Forwarded-For.
* Every rate is an environment variable (settings.THROTTLE_RATES); windows
  such as "30/5m" are supported.
* If the counter store fails, requests are allowed (auth must not 500 because
  a cache is down) and the degraded state is logged for Sentry.
"""
import hashlib
import logging
import time

# pyre-ignore[missing-module]
from rest_framework.throttling import SimpleRateThrottle

from config.client_ip import get_client_ip
from config.rate_parsing import parse_rate

logger = logging.getLogger(__name__)

_DEGRADED_LOG_INTERVAL_S = 60
_last_degraded_log = 0.0


def _report_degraded(scope, exc):
    global _last_degraded_log
    now = time.monotonic()
    if now - _last_degraded_log < _DEGRADED_LOG_INTERVAL_S:
        return
    _last_degraded_log = now
    logger.error(
        'Throttle store unavailable; allowing request (rate limiting degraded)',
        extra={'scope': scope, 'error_type': type(exc).__name__},
    )


class SimameThrottle(SimpleRateThrottle):
    """Proxy-aware identity, flexible windows, fail-open on store errors."""

    def get_ident(self, request):
        return get_client_ip(request)

    def parse_rate(self, rate):
        return parse_rate(rate)

    def allow_request(self, request, view):
        try:
            allowed = super().allow_request(request, view)
        except Exception as exc:  # cache/Redis outage must not break auth
            _report_degraded(self.scope, exc)
            return True
        if not allowed:
            # Lets the 429 handler explain *which* limit applied.
            scopes = getattr(request, '_throttled_scopes', [])
            scopes.append(self.scope)
            request._throttled_scopes = scopes
        return allowed

    @staticmethod
    def _hash(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]


class IPThrottle(SimameThrottle):
    """Per client IP, for any caller."""

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class UserThrottle(SimameThrottle):
    """Per authenticated user; anonymous callers are not counted here."""

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}


class UserOrIPThrottle(SimameThrottle):
    """Per user when signed in, else per IP."""

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f'user:{request.user.pk}'
        else:
            ident = f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class _AuthAwareThrottle(SimameThrottle):
    """Signed-in and signed-out traffic get separate budgets.

    Signed-out traffic is counted per IP, so its limit must allow many
    customers browsing before sign-in from one campus or carrier IP.
    """

    user_scope = ''
    anon_scope = ''

    def __init__(self):
        # Rates are resolved per request once we know who is calling.
        pass

    def allow_request(self, request, view):
        authenticated = bool(request.user and request.user.is_authenticated)
        self.scope = self.user_scope if authenticated else self.anon_scope
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f'user:{request.user.pk}'
        else:
            ident = f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class BurstUserThrottle(_AuthAwareThrottle):
    user_scope = 'burst_user'
    anon_scope = 'burst_anon'


class SustainedUserThrottle(_AuthAwareThrottle):
    user_scope = 'sustained_user'
    anon_scope = 'sustained_anon'


def _normalized_account_value(request):
    data = getattr(request, 'data', None) or {}
    email = str(data.get('email', '') or '').strip().lower()
    if email:
        return f'email:{email}'
    phone = str(data.get('phone', '') or '').strip()
    if phone:
        return f'phone:{phone}'
    return ''


class AccountScopedThrottle(SimameThrottle):
    """Keyed on the email/phone being acted on, never on a shared IP.

    Requests without an identifier fall back to the IP so they are still
    bounded. The identifier is hashed so no email lands in cache keys.
    """

    def get_cache_key(self, request, view):
        account = _normalized_account_value(request)
        ident = f'acct:{self._hash(account)}' if account else f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


# -- Authentication -----------------------------------------------------------

class RegisterIPBurstThrottle(IPThrottle):
    scope = 'signup_ip_burst'


class RegisterIPHourlyThrottle(IPThrottle):
    scope = 'signup_ip_hourly'


class RegisterIPDailyThrottle(IPThrottle):
    scope = 'signup_ip_daily'


class RegisterAccountThrottle(AccountScopedThrottle):
    scope = 'signup_account'


REGISTER_THROTTLES = [
    RegisterIPBurstThrottle, RegisterIPHourlyThrottle, RegisterIPDailyThrottle, RegisterAccountThrottle,
]


class LoginIPBurstThrottle(IPThrottle):
    scope = 'login_ip_burst'


class LoginIPHourlyThrottle(IPThrottle):
    scope = 'login_ip_hourly'


class LoginAccountBurstThrottle(AccountScopedThrottle):
    scope = 'login_account_burst'


class LoginAccountHourlyThrottle(AccountScopedThrottle):
    scope = 'login_account_hourly'


LOGIN_THROTTLES = [
    LoginIPBurstThrottle, LoginIPHourlyThrottle, LoginAccountBurstThrottle, LoginAccountHourlyThrottle,
]


class SocialLoginIPBurstThrottle(IPThrottle):
    """Exchange of a Clerk session token; Clerk rate-limits sign-in itself."""
    scope = 'social_ip_burst'


class SocialLoginIPHourlyThrottle(IPThrottle):
    scope = 'social_ip_hourly'


SOCIAL_LOGIN_THROTTLES = [SocialLoginIPBurstThrottle, SocialLoginIPHourlyThrottle]


class RefreshTokenThrottle(SimameThrottle):
    """Per refresh token: stops one client's retry storm without touching
    thousands of customers who share a carrier IP and refresh every 10 min."""
    scope = 'refresh_token'

    def get_cache_key(self, request, view):
        token = str((getattr(request, 'data', None) or {}).get('refresh', '') or '')
        ident = f'tok:{self._hash(token)}' if token else f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class RefreshIPThrottle(IPThrottle):
    scope = 'refresh_ip'


REFRESH_THROTTLES = [RefreshTokenThrottle, RefreshIPThrottle]


class PasswordResetIPThrottle(IPThrottle):
    scope = 'password_reset_ip'


class PasswordResetAccountThrottle(AccountScopedThrottle):
    scope = 'password_reset_account'


class PasswordResetAccountDailyThrottle(AccountScopedThrottle):
    scope = 'password_reset_account_daily'


PASSWORD_RESET_THROTTLES = [
    PasswordResetIPThrottle, PasswordResetAccountThrottle, PasswordResetAccountDailyThrottle,
]


class ResetPasswordIPThrottle(IPThrottle):
    scope = 'reset_password_ip'


class ResetPasswordTokenThrottle(SimameThrottle):
    scope = 'reset_password_token'

    def get_cache_key(self, request, view):
        token = str((getattr(request, 'data', None) or {}).get('token', '') or '')
        ident = f'tok:{self._hash(token)}' if token else f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


RESET_PASSWORD_THROTTLES = [ResetPasswordIPThrottle, ResetPasswordTokenThrottle]


# -- Marketplace / commerce ---------------------------------------------------

class ReviewThrottle(UserOrIPThrottle):
    scope = 'review'


class FeedbackThrottle(UserOrIPThrottle):
    scope = 'feedback'


class LegalPublicThrottle(IPThrottle):
    scope = 'legal_public'


class PaymentCreateThrottle(UserOrIPThrottle):
    scope = 'payment_create'


class CouponValidateThrottle(UserThrottle):
    """Stops promo-code guessing from one account."""
    scope = 'coupon_validate'


class CouponValidateDailyThrottle(UserThrottle):
    scope = 'coupon_validate_daily'


COUPON_THROTTLES = [CouponValidateThrottle, CouponValidateDailyThrottle]


class ReferralApplyThrottle(UserThrottle):
    scope = 'referral_apply'


class MediaUploadThrottle(UserThrottle):
    scope = 'media_upload'


class AdminSearchThrottle(UserThrottle):
    scope = 'admin_search'


class NotifTrackThrottle(UserThrottle):
    """Per-user limit on notification open/click tracking events."""
    scope = 'notif_track'


# -- Backwards-compatible aliases (imported by existing views) ---------------
LoginIPThrottle = LoginIPBurstThrottle
LoginAccountThrottle = LoginAccountBurstThrottle
RegisterIPThrottle = RegisterIPBurstThrottle
