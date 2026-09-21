"""Resolve the real client IP behind Render's proxies.

X-Forwarded-For is a list the client can pre-seed with anything; every proxy
then *appends* the address it received the connection from. Only the entries
added by proxies we operate behind are trustworthy, so the client address is
the TRUSTED_PROXY_COUNT-th entry from the right, never the leftmost one.

Settings (environment-configurable, see config/settings.py):
  CLIENT_IP_HEADER     optional META key set by the edge that clients cannot
                       forge (e.g. HTTP_TRUE_CLIENT_IP). Takes precedence.
  TRUSTED_PROXY_COUNT  how many proxies append to X-Forwarded-For.
"""
import ipaddress

from django.conf import settings


def _valid_ip(value):
    if not value:
        return None
    candidate = value.strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def get_client_ip(request):
    meta = request.META

    header = getattr(settings, 'CLIENT_IP_HEADER', '')
    if header:
        trusted = _valid_ip(meta.get(header, ''))
        if trusted:
            return trusted

    hops = getattr(settings, 'TRUSTED_PROXY_COUNT', 0)
    forwarded = meta.get('HTTP_X_FORWARDED_FOR', '')
    if hops > 0 and forwarded:
        chain = [part.strip() for part in forwarded.split(',') if part.strip()]
        if len(chain) >= hops:
            resolved = _valid_ip(chain[-hops])
            if resolved:
                return resolved

    return _valid_ip(meta.get('REMOTE_ADDR', '')) or 'unknown'
