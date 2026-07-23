"""Convert database-connectivity errors into a graceful 503 response.

Catches ``OperationalError`` / ``InterfaceError`` raised *during view processing*
(e.g. the admin dashboard's aggregate queries) and returns a structured 503
instead of a raw HTTP 500. Errors raised in *pre-view* middleware (session/user
lookup) are guarded at their source in ``DeactivationMiddleware`` because
``process_exception`` does not fire for those.

Placed immediately after ``JSONErrorMiddleware`` so its ``process_exception``
runs first for database errors, while all other errors still fall through to the
generic JSON 500 handler.
"""
import logging

from config.resilience import database_unavailable_response, is_database_unavailable

logger = logging.getLogger(__name__)


class DatabaseAvailabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not is_database_unavailable(exception):
            return None
        logger.error(
            "Database unavailable during request to %s: %s",
            request.path,
            exception,
            exc_info=True,
            extra={'request': request},
        )
        return database_unavailable_response(request)
