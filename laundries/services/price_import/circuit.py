"""Provider circuit breaker and OCR quota counter.

State lives in the shared ``throttle`` cache alias (Redis when present, a
Postgres table in production, which has no Redis), so every gunicorn worker
sees the same state. Every cache error fails *open*: a broken cache must never
stop owners from importing, only make us a little less clever about routing.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import caches
from django.utils import timezone

from .errors import ProviderErrorKind

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3
FAILURE_WINDOW_SECONDS = 300
OPEN_SECONDS = 120
# Bad/revoked credentials will not heal by themselves; back off longer.
AUTH_OPEN_SECONDS = 600
# Daily quota exhausted: stop wasting ~2s per scan re-asking.
DAILY_QUOTA_OPEN_SECONDS = 1800


def _cache():
    return caches['throttle']


def _key(provider: str) -> str:
    return f'price_import:circuit:{provider}'


def is_open(provider: str) -> bool:
    try:
        state = _cache().get(_key(provider)) or {}
    except Exception:
        logger.warning('price_import circuit cache unavailable; treating as closed')
        return False
    return state.get('open_until', 0) > time.time()


def record_success(provider: str) -> None:
    try:
        _cache().delete(_key(provider))
    except Exception:
        pass


def record_failure(provider: str, kind: str, *, daily_quota: bool = False) -> None:
    if kind not in ProviderErrorKind.TRIPS_CIRCUIT:
        return
    now = time.time()
    try:
        cache = _cache()
        state = cache.get(_key(provider)) or {}
        failures = [t for t in state.get('failures', []) if t > now - FAILURE_WINDOW_SECONDS]
        failures.append(now)
        state['failures'] = failures[-10:]
        if kind == ProviderErrorKind.AUTH:
            state['open_until'] = now + AUTH_OPEN_SECONDS
        elif daily_quota:
            state['open_until'] = now + DAILY_QUOTA_OPEN_SECONDS
        elif len(failures) >= FAILURE_THRESHOLD:
            state['open_until'] = now + OPEN_SECONDS
        if state.get('open_until', 0) > now:
            logger.warning(
                'price_import circuit opened',
                extra={'provider': provider, 'kind': kind, 'failures': len(failures)},
            )
        cache.set(_key(provider), state, FAILURE_WINDOW_SECONDS + DAILY_QUOTA_OPEN_SECONDS)
    except Exception:
        logger.warning('price_import circuit cache unavailable; failure not recorded')


# --- OCR.space monthly Engine 3 budget ---------------------------------------

def _ocr_month_key() -> str:
    return f'price_import:ocr_calls:{timezone.now():%Y-%m}'


def ocr_calls_this_month() -> int:
    try:
        return int(_cache().get(_ocr_month_key()) or 0)
    except Exception:
        return 0


def ocr_quota_available() -> bool:
    return ocr_calls_this_month() < settings.OCR_SPACE_MONTHLY_LIMIT


def record_ocr_call() -> None:
    try:
        cache = _cache()
        key = _ocr_month_key()
        cache.set(key, int(cache.get(key) or 0) + 1, 40 * 86400)
    except Exception:
        pass
