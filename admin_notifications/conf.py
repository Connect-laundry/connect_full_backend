"""Typed, validated access to the admin-notification settings.

Every value comes from ``django.conf.settings`` (populated from the
environment in config/settings.py), read at call time so tests and an
emergency env change both take effect without a code path caching them.
Secrets are only ever read here and handed to the providers; nothing in this
module logs or formats them.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from .phone import AdminRecipientError, normalize_admin_recipient

PRODUCTION = 'production'


def _get(name, default=None):
    return getattr(settings, name, default)


def _flag(name) -> bool:
    value = _get(name, False)
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def notifications_enabled() -> bool:
    """Master switch. Off when the env var is missing."""
    return _flag('ADMIN_ORDER_NOTIFICATIONS_ENABLED')


def sms_enabled() -> bool:
    return notifications_enabled() and _flag('ADMIN_SMS_NOTIFICATIONS_ENABLED')


def whatsapp_enabled() -> bool:
    return notifications_enabled() and _flag('ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED')


def environment() -> str:
    return (str(_get('ADMIN_NOTIFICATION_ENVIRONMENT', '') or 'development')).strip().lower()


def is_production() -> bool:
    return environment() == PRODUCTION


def message_prefix() -> str:
    """Non-production messages are always marked so nobody acts on them."""
    return '' if is_production() else f'[{environment().upper()}] '


@dataclass(frozen=True)
class RecipientList:
    valid: tuple[str, ...]
    invalid: tuple[str, ...]


def parse_recipients(raw) -> RecipientList:
    """Parse, normalize (233XXXXXXXXX) and de-duplicate a recipient list.

    Malformed entries are returned separately so the config check can fail
    loudly; they are never sent to.
    """
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw or '').split(',')
    valid, invalid = [], []
    for item in items:
        if not item.strip():
            continue
        try:
            number = normalize_admin_recipient(item)
        except AdminRecipientError:
            invalid.append(item.strip())
            continue
        if number not in valid:
            valid.append(number)
    return RecipientList(tuple(valid), tuple(invalid))


def sms_recipients() -> tuple[str, ...]:
    return parse_recipients(_get('ADMIN_SMS_RECIPIENTS', '')).valid


def whatsapp_recipients() -> tuple[str, ...]:
    return parse_recipients(_get('ADMIN_WHATSAPP_RECIPIENTS', '')).valid


def sms_detail_mode() -> str:
    mode = str(_get('ADMIN_SMS_DETAIL_MODE', 'full') or 'full').strip().lower()
    return mode if mode in ('full', 'compact') else 'full'


def sms_max_chars() -> int:
    # 6 concatenated GSM-7 segments. Arkesel concatenates long messages
    # itself ("a 200 character message will be 2 pages"); past this size the
    # text is split into labelled parts so no handset/network cap truncates it.
    return max(300, int(_get('ADMIN_SMS_MAX_CHARS_PER_MESSAGE', 918) or 918))


def whatsapp_max_body_chars() -> int:
    # Meta rejects a template whose hydrated body exceeds 1024 characters.
    return max(400, int(_get('ADMIN_WHATSAPP_MAX_BODY_CHARS', 1024) or 1024))


def max_attempts() -> int:
    return max(1, int(_get('ADMIN_NOTIFICATION_MAX_ATTEMPTS', 6) or 6))


def retry_schedule_seconds() -> tuple[int, ...]:
    raw = _get('ADMIN_NOTIFICATION_RETRY_SCHEDULE_SECONDS', '30,120,600,1800,7200')
    if isinstance(raw, (list, tuple)):
        values = [int(v) for v in raw]
    else:
        values = [int(v) for v in str(raw).split(',') if v.strip()]
    return tuple(v for v in values if v > 0) or (30, 120, 600, 1800, 7200)


def max_age_hours() -> int:
    return max(1, int(_get('ADMIN_NOTIFICATION_MAX_AGE_HOURS', 12) or 12))


def claim_lease_seconds() -> int:
    return max(60, int(_get('ADMIN_NOTIFICATION_CLAIM_LEASE_SECONDS', 600) or 600))


def batch_size() -> int:
    return max(1, int(_get('ADMIN_NOTIFICATION_BATCH_SIZE', 25) or 25))


def dispatch_in_thread() -> bool:
    return bool(_get('ADMIN_NOTIFICATION_DISPATCH_IN_THREAD', True))


def sweep_enabled() -> bool:
    return bool(_get('ADMIN_NOTIFICATION_SWEEP_ENABLED', True))


def sweep_seconds() -> int:
    return max(5, int(_get('ADMIN_NOTIFICATION_SWEEP_SECONDS', 30) or 30))


def http_timeouts() -> tuple[float, float]:
    return (
        float(_get('ADMIN_NOTIFICATION_HTTP_CONNECT_TIMEOUT', 5) or 5),
        float(_get('ADMIN_NOTIFICATION_HTTP_READ_TIMEOUT', 15) or 15),
    )


# Hosts that must never appear in a production alert or callback URL. The
# first is the old staging service, suspended since 2026-08 and still the
# non-DEBUG default of ADMIN_BASE_URL in config/settings.py.
DEFAULT_FORBIDDEN_HOSTS = ('connect-full-backend.onrender.com',)


def forbidden_hosts() -> tuple[str, ...]:
    raw = _get('ADMIN_NOTIFICATION_FORBIDDEN_HOSTS', None)
    if raw is None:
        return DEFAULT_FORBIDDEN_HOSTS
    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(',')
    return tuple(h.strip().lower() for h in items if h.strip())


def url_problem(url: str) -> str:
    """Why ``url`` is unfit as a production origin, or '' when it is fine."""
    from urllib.parse import urlparse
    if not url:
        return 'not set'
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        return 'must be an https:// origin'
    host = parsed.hostname.lower()
    if host in forbidden_hosts():
        return f'points at {host}, which is not the production backend'
    if host in ('localhost', '127.0.0.1') or host.endswith(('.local', '.test', '.example')):
        return f'points at a non-production host ({host})'
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        return 'must be an origin only (no path)'
    return ''


def admin_base_url() -> str:
    """Origin for admin order links.

    Production uses ADMIN_NOTIFICATION_ADMIN_BASE_URL only, never a fallback:
    a link to the wrong (suspended staging) server is worse than none. If it
    is missing or invalid there, '' is returned and messages say so.
    """
    explicit = str(_get('ADMIN_NOTIFICATION_ADMIN_BASE_URL', '') or '').strip().rstrip('/')
    if is_production():
        return '' if url_problem(explicit) else explicit
    return explicit or str(_get('ADMIN_BASE_URL', '') or '').rstrip('/')


# --- Arkesel SMS ---------------------------------------------------------

def arkesel_api_key() -> str:
    return str(_get('ARKESEL_API_KEY', '') or '').strip()


def arkesel_sender_id() -> str:
    return str(_get('ARKESEL_SENDER_ID', 'SIMAME') or '').strip()


def arkesel_sms_base_url() -> str:
    return str(_get('ARKESEL_SMS_BASE_URL', 'https://sms.arkesel.com') or '').rstrip('/')


def arkesel_sms_sandbox() -> bool:
    """Sandbox unless explicitly turned off. Production must set it false."""
    value = _get('ARKESEL_SMS_SANDBOX', None)
    if value is None or value == '':
        return not is_production()
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def arkesel_callback_secret() -> str:
    return str(_get('ARKESEL_SMS_CALLBACK_SECRET', '') or '').strip()


def arkesel_callback_base_url() -> str:
    explicit = str(_get('ARKESEL_SMS_CALLBACK_BASE_URL', '') or '').strip().rstrip('/')
    if is_production():
        return '' if url_problem(explicit) else explicit
    return explicit or str(_get('ADMIN_BASE_URL', '') or '').rstrip('/')


def arkesel_callback_url() -> str:
    """Full delivery-report URL, or '' when callbacks are not configured."""
    secret = arkesel_callback_secret()
    base = arkesel_callback_base_url()
    if not secret or not base:
        return ''
    from django.urls import reverse
    return base + reverse('arkesel-sms-status', kwargs={'token': secret})


# --- Arkesel WhatsApp (KOVA IQ external API) -------------------------------

def arkesel_whatsapp_base_url() -> str:
    return str(_get('ARKESEL_WHATSAPP_API_BASE_URL', 'https://kova-api.arkesel.com') or '').rstrip('/')


def arkesel_whatsapp_token() -> str:
    return str(_get('ARKESEL_WHATSAPP_API_TOKEN', '') or '').strip()


def arkesel_whatsapp_org_id() -> str:
    return str(_get('ARKESEL_WHATSAPP_ORGANIZATION_ID', '') or '').strip()


def arkesel_whatsapp_inbox_id() -> str:
    return str(_get('ARKESEL_WHATSAPP_INBOX_ID', '') or '').strip()


def whatsapp_template_id(key: str) -> str:
    setting = {
        'new_order': 'ARKESEL_WHATSAPP_NEW_ORDER_TEMPLATE_ID',
        'order_update': 'ARKESEL_WHATSAPP_ORDER_UPDATE_TEMPLATE_ID',
    }[key]
    return str(_get(setting, '') or '').strip()


def fingerprint(secret: str) -> str:
    """A short, non-reversible identifier for a secret (for config reports)."""
    if not secret:
        return 'missing'
    import hashlib
    return 'sha256:' + hashlib.sha256(secret.encode('utf-8')).hexdigest()[:8]


# --- Telegram (official Bot API) -----------------------------------------------

def telegram_enabled() -> bool:
    return notifications_enabled() and _flag('ADMIN_TELEGRAM_NOTIFICATIONS_ENABLED')


def telegram_bot_token() -> str:
    return str(_get('TELEGRAM_BOT_TOKEN', '') or '').strip()


def telegram_api_base_url() -> str:
    return str(_get('TELEGRAM_API_BASE_URL', 'https://api.telegram.org') or '').rstrip('/')


def parse_telegram_chat_ids(raw) -> RecipientList:
    """Numeric chat ids (groups are negative, e.g. -1001234567890), de-duplicated."""
    import re
    items = raw if isinstance(raw, (list, tuple)) else str(raw or '').split(',')
    valid, invalid = [], []
    for item in items:
        value = str(item).strip()
        if not value:
            continue
        if re.fullmatch(r'-?\d{5,19}', value):
            if value not in valid:
                valid.append(value)
        else:
            invalid.append(value)
    return RecipientList(tuple(valid), tuple(invalid))


def telegram_chat_ids() -> tuple[str, ...]:
    return parse_telegram_chat_ids(_get('ADMIN_TELEGRAM_CHAT_IDS', '')).valid
