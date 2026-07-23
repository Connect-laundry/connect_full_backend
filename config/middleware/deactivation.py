# pyre-ignore[missing-module]
from django.utils.deprecation import MiddlewareMixin
# pyre-ignore[missing-module]
from django.db.utils import InterfaceError, OperationalError
# pyre-ignore[missing-module]
from django.http import JsonResponse
# pyre-ignore[missing-module]
from rest_framework import status

from config.resilience import database_unavailable_response

class DeactivationMiddleware(MiddlewareMixin):
    """
    Middleware to block inactive users and handle global deactivation rules.
    """
    def process_request(self, request):
        # Resolving request.user lazily reads the session from the database. If
        # the database is unavailable this is the first place the outage surfaces
        # (before any view runs, so process_exception hooks never fire). Degrade
        # gracefully to a structured 503 instead of a raw HTTP 500.
        try:
            user_is_authenticated = request.user.is_authenticated
        except (OperationalError, InterfaceError):
            return database_unavailable_response(request)

        if not user_is_authenticated:
            return None

        # 1. Block inactive users (request.user is already resolved above)
        if not request.user.is_active:
            return JsonResponse({
                "status": "error",
                "message": "Your account has been deactivated. Please contact support.",
                "data": {
                    "reason": getattr(request.user, 'deactivation_reason', 'Account disabled')
                }
            }, status=status.HTTP_403_FORBIDDEN)

        # 2. Prevent inactive users from performing actions (extra layer)
        # This is already handled by is_active=False usually in Django's ModelBackend,
        # but for custom auth or specific app logic it's good to be explicit.
        
        return None
