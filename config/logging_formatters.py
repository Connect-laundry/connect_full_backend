import logging
import json
# pyre-ignore[missing-module]
from pythonjsonlogger import jsonlogger

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter to include request data and user info if available.
    """
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        
        # Add request context if running in a request-response cycle
        # pyre-ignore[import]
        from django.http import HttpRequest
        import threading
        
        # Note: In a production environment, we might use local thread storage 
        # to pass the request object here, but for now we'll rely on the logger having 'request' in extra.
        request = log_record.get('request')
        if isinstance(request, HttpRequest):
            log_record['path'] = request.path
            log_record['method'] = request.method
            log_record['request_id'] = getattr(request, 'request_id', 'N/A')
            if request.user.is_authenticated:
                log_record['user_id'] = str(request.user.id)
                log_record['user_email'] = request.user.email
            else:
                log_record['user_id'] = 'Anonymous'
        
        # Add log level and timestamp if not already there
        if not log_record.get('level'):
            log_record['level'] = record.levelname
        if not log_record.get('timestamp'):
            # pyre-ignore[import]
            from django.utils import timezone
            log_record['timestamp'] = timezone.now().isoformat()
