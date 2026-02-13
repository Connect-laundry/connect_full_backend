# pyre-ignore[missing-module]
from django.utils.deprecation import MiddlewareMixin
# pyre-ignore[missing-module]
from django.core.cache import cache
# pyre-ignore[missing-module]
from django.http import JsonResponse
import os

class LoginLockoutMiddleware(MiddlewareMixin):
    """
    Redis-backed login lockout middleware.
    Locks an IP address after 5 failed attempts at the login endpoint.
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path == '/api/v1/auth/login/' and request.method == 'POST':
            ip_address = self.get_client_ip(request)
            lockout_key = f"lockout_{ip_address}"
            
            if cache.get(lockout_key):
                return JsonResponse({
                    "status": "error",
                    "message": "Too many failed login attempts. Your account is locked for 15 minutes.",
                    "data": {}
                }, status=429)

    def process_response(self, request, response):
        # We check for 401 Unauthorized specifically on login endpoint
        if request.path == '/api/v1/auth/login/' and request.method == 'POST' and response.status_code == 401:
            ip_address = self.get_client_ip(request)
            attempts_key = f"login_attempts_{ip_address}"
            
            attempts = cache.get(attempts_key, 0) + 1
            cache.set(attempts_key, attempts, 3600) # 1 hour track
            
            if attempts >= int(os.getenv('MAX_LOGIN_ATTEMPTS', 5)):
                lockout_duration = int(os.getenv('LOCKOUT_DURATION_SECONDS', 900)) # 15 mins
                cache.set(f"lockout_{ip_address}", True, lockout_duration)
                cache.delete(attempts_key)
        
        # Reset on success
        if request.path == '/api/v1/auth/login/' and request.method == 'POST' and response.status_code == 200:
            ip_address = self.get_client_ip(request)
            cache.delete(f"login_attempts_{ip_address}")

        return response

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
