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
        logger.error(f"DRF Exception at {
                context['request'].path}: {
                str(exc)}", exc_info=True)

        data = {
            "success": False,
            "status": "error",
            "message": "An internal server error occurred.",
            "data": {},
        }
        return Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ... existing processing ...
    if response is not None:
        # If it's a throttle exception, customize the message
        if isinstance(exc, Throttled):
            custom_data = {
                "success": False,
                "status": "error",
                "message": f"Too many requests. Please try again in {
                    exc.wait} seconds.",
                "data": {},
            }
            response.data = custom_data
        else:
            # Handle other errors to fit the envelope if they don't already
            if not ("success" in response.data and "message" in response.data):
                message = "An error occurred."
                if "detail" in response.data:
                    message = response.data["detail"]

                response.data = {
                    "success": False,
                    "status": "error",
                    "message": message,
                    "data": response.data,
                }

    return response
