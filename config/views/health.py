import logging
import os
# pyre-ignore[missing-module]
from django.db import connections
# pyre-ignore[missing-module]
from django.db.utils import OperationalError
# pyre-ignore[missing-module]
from django.http import JsonResponse
# pyre-ignore[missing-module]
from django_redis import get_redis_connection
# pyre-ignore[missing-module]
from config.celery import app as celery_app

logger = logging.getLogger(__name__)

def health_check(request):
    """
    Production health check endpoint.

    Public callers only receive a minimal liveness signal.
    Internal callers may opt into component details with a shared token.
    """
    internal_token = os.getenv('INTERNAL_HEALTH_TOKEN', '').strip()
    provided_token = request.headers.get('X-Health-Token', '').strip()
    include_components = bool(internal_token) and provided_token == internal_token

    components_status = {
        "database": "down",
        "redis": "down",
        "celery": "down"
    }
    
    health_status = {
        "status": "healthy",
        "components": components_status
    }
    
    # 1. Check Database
    try:
        db_conn = connections['default']
        db_conn.cursor()
        components_status['database'] = "up"
    except OperationalError:
        health_status['status'] = "degraded"
        logger.error("Health Check: Database is DOWN")

    # 2. Check Redis
    try:
        redis_conn = get_redis_connection("default")
        redis_conn.ping()
        components_status['redis'] = "up"
    except Exception as e:
        health_status['status'] = "degraded"
        logger.error(f"Health Check: Redis is DOWN - {str(e)}")

    # 3. Check Celery Broker
    try:
        with celery_app.broker_connection() as conn:
            conn.ensure_connection(max_retries=1)
            components_status['celery'] = "up"
    except Exception as e:
        health_status['status'] = "degraded"
        logger.error(f"Health Check: Celery Broker is DOWN - {str(e)}")

    status_code = 200 if health_status['status'] == "healthy" else 503
    response_payload = {
        "status": health_status["status"],
    }
    if include_components:
        response_payload["components"] = components_status

    response = JsonResponse(response_payload, status=status_code)
    response["Cache-Control"] = "no-store"
    return response
