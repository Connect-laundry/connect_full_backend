from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from django.db import connection, connections
from django.conf import settings

class DiagnosisView(APIView):
    permission_classes = [IsAdminUser]
    http_method_names = ['get']
    
    def get(self, request):
        if not settings.DEBUG:
            return Response(
                {"status": "error", "message": "Internal diagnostics are disabled."},
                status=404,
            )

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

        # Check for pending migrations
        pending_migrations = []
        try:
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connections['default'])
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            pending_migrations = [str(p[0]) for p in plan]
        except Exception:
            pending_migrations = []

        return Response({
            "status": "success",
            "diagnosis_version": "internal-admin-only",
            "message": "Internal diagnostics are enabled only in development.",
            "data": {
                "db_connection": db_conn,
                "postgis": bool(postgis_available),
                "debug_mode": settings.DEBUG,
                "pending_migrations_count": len(pending_migrations),
            }
        })
