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
        import traceback
        traceback.print_exc()  # Force traceback to stdout for Render logs
        logger.error(f"Unhandled exception at {request.path}: {exception}", exc_info=True)
        
        # Temporarily include the exact exception in the response for production debugging
        # We will remove this once the 500 error is resolved.
        message = f"Server Error: {str(exception)}"
            
        data = {
            "status": "error",
            "message": message,
            "data": {
                "traceback": traceback.format_exc()
            }
        }
        
        return JsonResponse(data, status=500)
