from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.db import connection, connections
from django.conf import settings
from django.core.management import call_command
import os
import io

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
                # Use exists() for a light query
                model_class.objects.all()[:1].exists()
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
            "message": "Use POST to trigger migrations remotely.",
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

    def post(self, request):
        """
        Trigger python manage.py migrate remotely.
        """
        out = io.StringIO()
        try:
            call_command('migrate', interactive=False, stdout=out)
            result = out.getvalue()
            return Response({
                "status": "success",
                "message": "Migration completed.",
                "output": result
            })
        except Exception as e:
            return Response({
                "status": "error",
                "message": f"Migration failed: {str(e)}",
                "output": out.getvalue()
            }, status=500)
