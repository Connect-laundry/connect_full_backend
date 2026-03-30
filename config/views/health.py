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
    Handles database, cache, and celery status.
    """
    components_status = {
        "database": "down",
        "cache": "down",
        "celery": "down",
    }

    health_status = {"status": "healthy", "components": components_status}

    # 1. Check Database (Essential)
    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        components_status["database"] = "up"
    except (OperationalError, Exception) as e:
        health_status["status"] = "down"
        logger.error(f"Health Check: Database is DOWN - {str(e)}")
        return JsonResponse(health_status, status=503)

    # 2. Check Cache (Database backed)
    try:
        from django.core.cache import cache

        cache.set("health_check", "ok", 10)
        if cache.get("health_check") == "ok":
            components_status["cache"] = "up"
        else:
            components_status["cache"] = "down"
    except Exception as e:
        health_status["status"] = "degraded"
        logger.warning(f"Health Check: Cache is DOWN - {str(e)}")

    # 3. Check Celery (Eager mode)
    try:
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            components_status["celery"] = "up (eager)"
        else:
            from config.celery import app as celery_app

            with celery_app.broker_connection() as conn:
                conn.ensure_connection(max_retries=1)
                components_status["celery"] = "up"
    except Exception as e:
        health_status["status"] = "degraded"
        logger.warning(f"Health Check: Celery is DOWN - {str(e)}")

    # Determination of HTTP status code
    db_engine = connections["default"].settings_dict.get("ENGINE", "")
    is_sqlite = "sqlite" in db_engine
    skip_services = (
        is_sqlite or os.getenv("SKIP_HEALTH_SERVICES", "False").lower() == "true"
    )

    if health_status["status"] == "healthy" or skip_services:
        status_code = 200
    else:
        status_code = 503

    return JsonResponse(health_status, status=status_code)
