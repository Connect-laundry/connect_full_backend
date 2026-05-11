from django.http import JsonResponse
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class JSONErrorMiddleware:
    """
    Ensures that all 500 errors return a JSON response instead of HTML,
    especially for non-DRF views like Admin or middleware crashes.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        logger.error(
            "Unhandled exception at %s",
            request.path,
            exc_info=True,
            extra={'request': request},
        )

        message = "An internal server error occurred."
        if settings.DEBUG:
            message = f"Server Error: {str(exception)}"

        data = {
            "status": "error",
            "message": message,
            "data": {}
        }

        if hasattr(request, 'request_id'):
            data["request_id"] = request.request_id

        return JsonResponse(data, status=500)
