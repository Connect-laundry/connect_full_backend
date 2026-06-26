"""Read-only Sentry Issues API client for the Connect Insights → Errors panel.

Uses an org auth token (SENTRY_API_TOKEN) — distinct from the DSN, which can
only *send* events. Results are Redis-cached so the admin page never blocks on
Sentry, and every failure degrades gracefully to an empty/`configured=False`
payload rather than raising.
"""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_CACHE_KEY = 'insights:sentry:issues'
_CACHE_TTL = 120  # seconds
_TIMEOUT = 8


def _s(name):
    return getattr(settings, name, '')


def is_configured():
    return bool(_s('SENTRY_API_TOKEN') and _s('SENTRY_ORG_SLUG') and _s('SENTRY_PROJECT_SLUG'))


def _headers():
    return {'Authorization': f'Bearer {_s("SENTRY_API_TOKEN")}'}


def get_issues(limit=10, stats_period='24h', use_cache=True):
    """Return the top unresolved issues for the configured project.

    Shape: {"configured": bool, "issues": [...], "error": str|None}
    """
    if not is_configured():
        return {"configured": False, "issues": [], "error": None}

    if use_cache:
        cached = cache.get(_CACHE_KEY)
        if cached is not None:
            return cached

    url = (f"{settings.SENTRY_API_BASE}/projects/"
           f"{settings.SENTRY_ORG_SLUG}/{settings.SENTRY_PROJECT_SLUG}/issues/")
    params = {'query': 'is:unresolved', 'statsPeriod': stats_period,
              'limit': limit, 'sort': 'freq'}
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
        issues = [
            {
                'title': item.get('title') or item.get('metadata', {}).get('type', 'Error'),
                'culprit': item.get('culprit', ''),
                'level': item.get('level', ''),
                'count': item.get('count', '0'),
                'user_count': item.get('userCount', 0),
                'permalink': item.get('permalink', ''),
                'last_seen': item.get('lastSeen', ''),
            }
            for item in (raw if isinstance(raw, list) else [])
        ]
        result = {"configured": True, "issues": issues, "error": None}
        cache.set(_CACHE_KEY, result, _CACHE_TTL)
        return result
    except Exception as exc:  # broad on purpose — the admin page must never break
        logger.warning("Sentry issues fetch failed", extra={'error': str(exc)})
        return {"configured": True, "issues": [], "error": 'Could not reach Sentry.'}


def get_summary(stats_period='24h'):
    """Headline counts derived from the issues list (open issues, affected users)."""
    data = get_issues(limit=100, stats_period=stats_period)
    issues = data['issues']
    total_events = 0
    for i in issues:
        try:
            total_events += int(i['count'])
        except (TypeError, ValueError):
            pass
    return {
        "configured": data["configured"],
        "error": data["error"],
        "open_issues": len(issues),
        "events_24h": total_events,
        "affected_users": sum(i.get('user_count', 0) or 0 for i in issues),
    }
