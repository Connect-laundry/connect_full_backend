# pyre-ignore[missing-module]
from rest_framework.renderers import JSONRenderer


class StandardResponseRenderer(JSONRenderer):
    """
    Custom renderer to ensure all responses follow a consistent JSON envelope.
    {
        "success": true | false,
        "status": "success" | "error",
        "message": "...",
        "data": {...}
    }
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if renderer_context is None:
            return super().render(data, accepted_media_type, renderer_context)

        response = renderer_context.get('response')
        if response is None:
            return super().render(data, accepted_media_type, renderer_context)

        # If response is already in our format, don't wrap it again
        if isinstance(
                data, dict) and (
                'success' in data and 'message' in data):
            if 'status' not in data:
                data['status'] = 'success' if data['success'] else 'error'
            return super().render(data, accepted_media_type, renderer_context)

        status_code = response.status_code
        is_success = status_code < 400

        message = "Operation successful."
        if status_code >= 400:
            message = "An error occurred."
            # Extract error message if available
            if isinstance(data, dict):
                if 'detail' in data:
                    message = data['detail']
                elif 'error' in data:
                    message = data['error']
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], str):
                message = data[0]

        res = {
            "success": is_success,
            "status": "success" if is_success else "error",
            "message": message,
            "data": data
        }

        return super().render(res, accepted_media_type, renderer_context)
