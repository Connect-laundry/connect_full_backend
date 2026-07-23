# pyre-ignore[missing-module]
from rest_framework.views import exception_handler
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from rest_framework.exceptions import Throttled
# pyre-ignore[missing-module]
from django.db.utils import InterfaceError, OperationalError
import logging

from config.resilience import RETRY_AFTER_SECONDS

logger = logging.getLogger(__name__)


def _sanitize_message(value):
    if isinstance(value, str):
        return value.replace('<', '').replace('>', '').strip()
    return value

def custom_exception_handler(exc, context):
    """
    Custom exception handler to ensure throttled requests and other errors
    return a consistent JSON envelope.
    """
    request = context.get('request')
    request_id = getattr(request, 'request_id', None)

    # Database unreachable (Supabase/Neon down, quota suspension, dropped pooler
    # connection) — degrade to a structured 503 instead of a 500.
    if isinstance(exc, (OperationalError, InterfaceError)):
        path = getattr(request, 'path', 'unknown')
        logger.error("Database unavailable (DRF) at %s", path, exc_info=True,
                     extra={'request': request})
        data = {
            "status": "error",
            "message": "Service temporarily unavailable. Please try again shortly.",
            "data": {},
        }
        if request_id:
            data["request_id"] = request_id
        response = Response(data, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response["Retry-After"] = str(RETRY_AFTER_SECONDS)
        return response

    response = exception_handler(exc, context)

    if response is None:
        path = getattr(request, 'path', 'unknown')
        logger.error("DRF Exception at %s", path, exc_info=True, extra={'request': request})

        data = {
            "status": "error",
            "message": "An internal server error occurred.",
            "data": {}
        }
        if request_id:
            data["request_id"] = request_id
        return Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if response is not None:
        if isinstance(exc, Throttled):
            custom_data = {
                "status": "error",
                "message": f"Too many requests. Please try again in {exc.wait} seconds.",
                "data": {}
            }
            if request_id:
                custom_data["request_id"] = request_id
            response.data = custom_data
        else:
            if not ('status' in response.data and 'message' in response.data):
                message = "An error occurred."
                if 'detail' in response.data:
                    message = _sanitize_message(response.data['detail'])

                response.data = {
                    "status": "error",
                    "message": message,
                    "data": response.data
                }
            elif isinstance(response.data, dict) and 'message' in response.data:
                response.data['message'] = _sanitize_message(response.data['message'])

            if request_id and isinstance(response.data, dict):
                response.data.setdefault('request_id', request_id)

    return response
