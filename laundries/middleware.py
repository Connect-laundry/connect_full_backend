import json
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
        logger.error(f"Unhandled exception at {request.path}: {exception}", exc_info=True)
        
        # Only handle if it's an API request or Admin request that we want to keep JSON (optional)
        # For now, let's satisfy the frontend requirement for ALL 500s to be JSON.
        
        data = {
            "status": "error",
            "message": f"Server Error: {str(exception)}" if settings.DEBUG else "An internal server error occurred.",
            "data": {}
        }
        
        return JsonResponse(data, status=500)
