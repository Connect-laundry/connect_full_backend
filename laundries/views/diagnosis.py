from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.db import connection, connections
from django.conf import settings
import os

class DiagnosisView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        db_conn = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                db_conn = True
        except Exception as e:
            db_conn = f"Failed: {str(e)}"
            
        postgis_available = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT postgis_full_version()")
                postgis_available = cursor.fetchone()[0]
        except Exception:
            postgis_available = "Not enabled or not PostGIS DB"

        def check_model_deep(model_class):
            try:
                # Try to fetch one full record to check all columns
                obj = model_class.objects.first()
                if obj:
                    # Force evaluation of all fields by converting to string
                    str(obj)
                return "OK"
            except Exception as e:
                return f"Error: {str(e)}"

        from laundries.models.laundry import Laundry
        from laundries.models.service import Service
        from laundries.models.review import Review
        from laundries.models.favorite import Favorite
        from ordering.models.base import Order
        
        # Check for pending migrations
        pending_migrations = []
        try:
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connections['default'])
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            pending_migrations = [str(p[0]) for p in plan]
        except Exception as e:
            pending_migrations = [f"Check Failed: {str(e)}"]

        return Response({
            "status": "success",
            "data": {
                "db_connection": db_conn,
                "postgis": postgis_available,
                "use_postgis_setting": os.getenv('USE_POSTGIS', 'False'),
                "debug_mode": settings.DEBUG,
                "pending_migrations": pending_migrations,
                "model_health": {
                    "Laundry": check_model_deep(Laundry),
                    "Service": check_model_deep(Service),
                    "Review": check_model_deep(Review),
                    "Favorite": check_model_deep(Favorite),
                    "Order": check_model_deep(Order),
                },
                "allowed_hosts": settings.ALLOWED_HOSTS,
            }
        })
