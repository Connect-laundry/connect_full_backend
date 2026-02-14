# pyre-ignore[missing-module]
from django.utils.deprecation import MiddlewareMixin
# pyre-ignore[missing-module]
from django.http import JsonResponse
# pyre-ignore[missing-module]
from rest_framework import status

class DeactivationMiddleware(MiddlewareMixin):
    """
    Middleware to block inactive users and handle global deactivation rules.
    """
    def process_request(self, request):
        if not request.user.is_authenticated:
            return None

        # 1. Block inactive users
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
