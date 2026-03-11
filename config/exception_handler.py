# pyre-ignore[missing-module]
from rest_framework.views import exception_handler
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from rest_framework.exceptions import Throttled
from django.conf import settings

def custom_exception_handler(exc, context):
    """
    Custom exception handler to ensure throttled requests and other errors
    return a consistent JSON envelope.
    """
    response = exception_handler(exc, context)

    if response is None:
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        
        # Log the full traceback to stdout (Render logs)
        print("\n=== CRITICAL API ERROR TRACEBACK ===")
        traceback.print_exc()
        print("=====================================\n")
        
        logger.error(f"DRF Exception at {context['request'].path}: {str(exc)}", exc_info=True)

        data = {
            "status": "error",
            "message": f"Server Error: {str(exc)}",
            "data": {
                "traceback": traceback.format_exc()
            }
        }
        return Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ... existing processing ...
    if response is not None:
        # If it's a throttle exception, customize the message
        if isinstance(exc, Throttled):
            custom_data = {
                "status": "error",
                "message": f"Too many requests. Please try again in {exc.wait} seconds.",
                "data": {}
            }
            response.data = custom_data
        else:
            # Handle other errors to fit the envelope if they don't already
            if not ('status' in response.data and 'message' in response.data):
                message = "An error occurred."
                if 'detail' in response.data:
                    message = response.data['detail']
                
                response.data = {
                    "status": "error",
                    "message": message,
                    "data": response.data
                }

    return response
