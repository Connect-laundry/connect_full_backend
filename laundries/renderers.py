# pyre-ignore[missing-module]
from rest_framework.renderers import JSONRenderer

class StandardResponseRenderer(JSONRenderer):
    """
    Custom renderer to ensure all responses follow a consistent JSON envelope.
    {
        "status": "success" | "error",
        "message": "...",
        "data": {...}
    }
    """
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context['response']
        
        # If response is already in our format, don't wrap it again
        if isinstance(data, dict) and ('status' in data and 'message' in data):
            return super().render(data, accepted_media_type, renderer_context)

        status_code = response.status_code
        status_text = "success" if status_code < 400 else "error"
        
        message = "Operation successful."
        if status_code >= 400:
            message = "An error occurred."
            # Extract error message if available
            if isinstance(data, dict):
                if 'detail' in data:
                    message = data['detail']
                elif 'error' in data:
                    message = data['error']
                # If it's a validation error, data itself is a dict of fields -> errors
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], str):
                 message = data[0]

        res = {
            "status": status_text,
            "message": message,
            "data": data
        }
        
        return super().render(res, accepted_media_type, renderer_context)
