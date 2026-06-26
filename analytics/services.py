"""Analytics ingestion + redaction helpers.

Single choke-point for writing AnalyticsEvent rows so PII redaction and event
caps are always applied — whether events come from the mobile batch endpoint or
from server-side signals.
"""
import logging

from .models import AnalyticsEvent

logger = logging.getLogger(__name__)

# Keys that must never be persisted in event_data, even if a client sends them.
_REDACT_KEYS = {
    'password', 'pass', 'pwd', 'token', 'access_token', 'refresh_token',
    'authorization', 'auth', 'secret', 'api_key', 'apikey', 'card', 'cvv',
    'pan', 'otp', 'pin', 'paystack_secret', 'email', 'phone',
}
_MAX_EVENT_DATA_KEYS = 50
_MAX_VALUE_LEN = 500
_REDACTED = '[redacted]'


def redact_event_data(data):
    """Return a shallow-sanitised copy of an event_data dict.

    Drops sensitive keys, truncates oversized values, and bounds the number of
    keys so a malicious client can't bloat a row. Non-dict input → {}.
    """
    if not isinstance(data, dict):
        return {}
    clean = {}
    for i, (key, value) in enumerate(data.items()):
        if i >= _MAX_EVENT_DATA_KEYS:
            break
        k = str(key)
        if k.lower() in _REDACT_KEYS:
            clean[k] = _REDACTED
            continue
        if isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
            value = value[:_MAX_VALUE_LEN]
        elif isinstance(value, (dict, list)):
            # Don't deep-store nested structures; keep analytics flat + cheap.
            value = _REDACTED if _contains_sensitive(value) else value
        clean[k] = value
    return clean


def _contains_sensitive(value):
    try:
        import json
        blob = json.dumps(value).lower()
    except (TypeError, ValueError):
        return True
    return any(s in blob for s in ('password', 'token', 'secret', 'cvv'))


class AnalyticsService:
    @staticmethod
    def record(event_name, *, user=None, session_id='', device_id='', platform='',
               os_version='', app_version='', screen_name='', event_data=None,
               occurred_at=None):
        """Persist a single event. Best-effort — never raises to the caller so
        analytics can never break a business flow."""
        try:
            return AnalyticsEvent.objects.create(
                event_name=str(event_name)[:64],
                user=user if (user and getattr(user, 'is_authenticated', False)) else None,
                session_id=str(session_id or '')[:64],
                device_id=str(device_id or '')[:128],
                platform=(platform or AnalyticsEvent.Platform.UNKNOWN),
                os_version=str(os_version or '')[:32],
                app_version=str(app_version or '')[:32],
                screen_name=str(screen_name or '')[:64],
                event_data=redact_event_data(event_data or {}),
                occurred_at=occurred_at,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Analytics record failed",
                         extra={'event_name': str(event_name), 'error': str(exc)})
            return None

    @staticmethod
    def record_server_event(event_name, *, user=None, event_data=None):
        """Convenience for server-side (signal) events."""
        return AnalyticsService.record(
            event_name, user=user,
            platform=AnalyticsEvent.Platform.SERVER,
            event_data=event_data or {},
        )
