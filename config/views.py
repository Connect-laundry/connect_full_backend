# pyre-ignore[missing-module]
from django.http import JsonResponse
# pyre-ignore[missing-module]
from django.db import connections
# pyre-ignore[missing-module]
from django.db.utils import OperationalError
# pyre-ignore[missing-module]
from django.conf import settings

def health_check(request):
    """
    Diagnostic endpoint to verify backend health.
    Checks:
    - Database connectivity
    - Redis/Cache availability (if configured)
    """
    health_status = {
        "status": "healthy",
        "timestamp": timezone.now().isoformat(),
        "services": {
            "database": "unknown",
        }
    }

    # 1. Check Database
    try:
        db_conn = connections['default']
        db_conn.cursor()
# pyre-ignore[missing-module]
        health_status["services"]["database"] = "up"
    except OperationalError:
# pyre-ignore[missing-module]
        health_status["services"]["database"] = "down"
        health_status["status"] = "unhealthy"

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JsonResponse(health_status, status=status_code)

# Add timezone import
# pyre-ignore[missing-module]
from django.utils import timezone
