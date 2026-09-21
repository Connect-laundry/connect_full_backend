"""Staging-only view of how the proxy chain presents the caller.

Needed to set TRUSTED_PROXY_COUNT / CLIENT_IP_HEADER from evidence rather than
guesswork: too few trusted hops lets clients spoof X-Forwarded-For past the IP
throttles; too many makes every customer behind one edge proxy share a bucket.

Returns only the caller's own request metadata. Disabled unless
IP_DIAGNOSTICS_ENABLED is true, which defaults to true only on staging.
"""
from django.conf import settings
from django.http import Http404, JsonResponse

FORWARDING_HEADERS = (
    'REMOTE_ADDR',
    'HTTP_X_FORWARDED_FOR',
    'HTTP_X_REAL_IP',
    'HTTP_TRUE_CLIENT_IP',
    'HTTP_CF_CONNECTING_IP',
    'HTTP_X_FORWARDED_PROTO',
    'HTTP_FORWARDED',
)


def request_ip_diagnostics(request):
    if not getattr(settings, 'IP_DIAGNOSTICS_ENABLED', False):
        raise Http404()
    from config.client_ip import get_client_ip

    headers = {name: request.META.get(name) for name in FORWARDING_HEADERS if request.META.get(name)}
    chain = [part.strip() for part in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if part.strip()]
    return JsonResponse({
        'headers': headers,
        'x_forwarded_for_hops': len(chain),
        'resolved_client_ip': get_client_ip(request),
        'trusted_proxy_count': getattr(settings, 'TRUSTED_PROXY_COUNT', None),
        'client_ip_header': getattr(settings, 'CLIENT_IP_HEADER', ''),
    })
