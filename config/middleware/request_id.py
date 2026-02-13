import uuid
# pyre-ignore[missing-module]
from django.utils.deprecation import MiddlewareMixin

class RequestIDMiddleware(MiddlewareMixin):
    """
    Middleware to inject a unique ID into every request.
    This ID is included in logs and returned in the 'X-Request-ID' header.
    """
    def process_request(self, request):
        request_id = request.META.get('HTTP_X_REQUEST_ID') or str(uuid.uuid4())
        request.request_id = request_id

    def process_response(self, request, response):
        if hasattr(request, 'request_id'):
            response['X-Request-ID'] = request.request_id
        return response
