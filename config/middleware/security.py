from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.deprecation import MiddlewareMixin


DOCS_PATH_PREFIXES = (
    '/api/schema/',
    '/api/docs/',
    '/api/redoc/',
)

API_CSP = "default-src 'none'; frame-ancestors 'none'"
DEV_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net unpkg.com; "
    "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net unpkg.com fonts.googleapis.com; "
    "img-src 'self' data: cdn.jsdelivr.net; "
    "font-src 'self' data: fonts.gstatic.com cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Middleware to inject essential security headers and strip sensitive ones.
    """
    def process_response(self, request, response):
        # Inject standard security headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        response['Cross-Origin-Opener-Policy'] = 'same-origin'
        response['Cross-Origin-Resource-Policy'] = 'same-origin'
        content_type = response.get('Content-Type', '')
        is_dev_docs = settings.DEBUG and any(
            request.path.startswith(prefix) for prefix in DOCS_PATH_PREFIXES
        )
        if is_dev_docs:
            response['Content-Security-Policy'] = DEV_DOCS_CSP
        elif request.path.startswith('/api/') or request.path == '/health/' or 'application/json' in content_type:
            response['Content-Security-Policy'] = API_CSP

        # Hide tech stack details
        if 'Server' in response:
            del response['Server']
        if 'X-Powered-By' in response:
            del response['X-Powered-By']
            
        return response
