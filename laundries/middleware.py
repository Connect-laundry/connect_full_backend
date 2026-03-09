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
        
        # Temporary unmask for final nearby verification
        message = f"Server Error: {str(exception)}"
            
        data = {
            "status": "error",
            "message": message,
            "data": {}
        }
        
        return JsonResponse(data, status=500)
