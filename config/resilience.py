"""Graceful degradation helpers for database outages.

When PostgreSQL (Supabase, or Neon during the transition) is unreachable, psycopg
raises errors that Django surfaces as ``OperationalError`` / ``InterfaceError``.
Without handling, these become a raw HTTP 500. Instead we return a structured
``503 Service Unavailable`` so the mobile apps and the admin can show a friendly
"temporarily unavailable" state and retry, rather than a bare "Server Error".

Used by:
  * ``config.exception_handler.custom_exception_handler`` (DRF / API views)
  * ``config.middleware.database_availability.DatabaseAvailabilityMiddleware``
    (view-origin errors in non-DRF views such as the Django admin)
  * ``config.middleware.deactivation.DeactivationMiddleware`` (pre-view session
    lookup, which is where the admin 500 actually originated)
"""
import logging

from django.db.utils import InterfaceError, OperationalError
from django.http import HttpResponse, JsonResponse
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

# Errors that mean "the database is unreachable / not responding" rather than a
# bug in application code: connection refused, quota suspension (Neon), dropped
# SSL connections, pooler timeouts. These are safe to translate into a 503.
DB_UNAVAILABLE_EXCEPTIONS = (OperationalError, InterfaceError)

RETRY_AFTER_SECONDS = 15

_MESSAGE = "Service temporarily unavailable. Please try again shortly."


def is_database_unavailable(exc):
    """True when ``exc`` indicates the database is unreachable (not a code bug)."""
    return isinstance(exc, DB_UNAVAILABLE_EXCEPTIONS)


def _wants_json(request):
    """Decide between a JSON envelope (apps/API) and an HTML page (admin/browser)."""
    if request.path.startswith('/api/'):
        return True
    accept = request.headers.get('Accept', '')
    if 'application/json' in accept and 'text/html' not in accept:
        return True
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def database_unavailable_response(request):
    """Return a structured 503 response for a database outage.

    JSON for API/app callers, a self-contained HTML page for browser/admin.
    The HTML template is rendered without any database access.
    """
    request_id = getattr(request, 'request_id', None)

    if _wants_json(request):
        payload = {
            "status": "error",
            "message": _MESSAGE,
            "data": {},
        }
        if request_id:
            payload["request_id"] = request_id
        response = JsonResponse(payload, status=503)
    else:
        try:
            html = render_to_string('503.html', {"request_id": request_id})
        except TemplateDoesNotExist:  # pragma: no cover - defensive fallback
            html = _MESSAGE
        response = HttpResponse(html, status=503, content_type='text/html; charset=utf-8')

    response["Retry-After"] = str(RETRY_AFTER_SECONDS)
    response["Cache-Control"] = "no-store"
    return response
