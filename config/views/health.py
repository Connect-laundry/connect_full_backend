import logging
import os
# pyre-ignore[missing-module]
from django.db import connections
# pyre-ignore[missing-module]
from django.db.utils import OperationalError
# pyre-ignore[missing-module]
from django.http import JsonResponse
# pyre-ignore[missing-module]
from django.conf import settings

logger = logging.getLogger(__name__)

def health_check(request):
    """
    Production health check endpoint.
    Handles database, redis, and celery status.
    """
    components_status = {
        "database": "down",
        "redis": "down",
        "celery": "down",
        "storage": "down"
    }
    
    health_status = {
        "status": "healthy",
        "components": components_status
    }
    
    # 1. Check Database (Essential)
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        components_status['database'] = "up"
    except (OperationalError, Exception) as e:
        health_status['status'] = "down"
        logger.error(f"Health Check: Database is DOWN - {str(e)}")
        return JsonResponse(health_status, status=503)

    # 2. Check Redis (Optional for smoke tests)
    try:
        # pyre-ignore[missing-module]
        from django_redis import get_redis_connection
        redis_conn = get_redis_connection("default")
        redis_conn.ping()
        components_status['redis'] = "up"
    except Exception as e:
        health_status['status'] = "degraded"
        logger.warning(f"Health Check: Redis is DOWN or not configured - {str(e)}")

    # 3. Check Celery Broker (Optional for smoke tests)
    try:
        # pyre-ignore[missing-module]
        from config.celery import app as celery_app
        with celery_app.broker_connection() as conn:
            conn.ensure_connection(max_retries=1)
            components_status['celery'] = "up"
    except Exception as e:
        health_status['status'] = "degraded"
        logger.warning(f"Health Check: Celery Broker is DOWN or not configured - {str(e)}")

    # Determination of HTTP status code
    # If DB is down, already returned 503.
    # If other services are down, we return 200 if SKIP_HEALTH_SERVICES is True,
    # or if we are running on SQLite (typical for smoke tests/local dev).
    # Otherwise we return 503 as per production hardening requirements.
    db_engine = connections['default'].settings_dict.get('ENGINE', '')
    is_sqlite = 'sqlite' in db_engine
    skip_services = is_sqlite or os.getenv('SKIP_HEALTH_SERVICES', 'False').lower() == 'true'
    
    if health_status['status'] == "healthy" or skip_services:
        status_code = 200
    else:
        status_code = 503
        
    return JsonResponse(health_status, status=status_code)
