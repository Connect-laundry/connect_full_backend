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
                # 1. Check if table exists
                if not model_class.objects.exists():
                    return "OK (Empty)"

                # 2. Try to fetch first record
                obj = model_class.objects.first()

                # 3. Special check for Laundry location
                if model_class.__name__ == "Laundry":
                    try:
                        loc = getattr(obj, "location", "MISSING_FIELD")
                        return f"OK (Location: {type(loc)})"
                    except Exception as e:
                        return f"Error on Location field: {str(e)}"

                # Check serialization of a record (dummy)
                str(obj)
                return "OK"
            except Exception as e:
                import traceback

                return f"Error: {
                    str(e)} | Details: {
                    traceback.format_exc().splitlines()[
                        -1]}"

        from laundries.models.laundry import Laundry
        from laundries.models.service import LaundryService
        from laundries.models.review import Review
        from laundries.models.favorite import Favorite
        from ordering.models.base import Order
        from marketplace.models.special_offer import SpecialOffer
        from marketplace.models.notification import Notification

        # Check for pending migrations
        pending_migrations = []
        try:
            from django.db.migrations.executor import MigrationExecutor

            executor = MigrationExecutor(connections["default"])
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            pending_migrations = [str(p[0]) for p in plan]
        except Exception as e:
            pending_migrations = [f"Check Failed: {str(e)}"]

        spatial_search_status = "N/A (Non-PostGIS)"
        if os.getenv("USE_POSTGIS", "False") == "True":
            try:
                from django.contrib.gis.geos import Point
                from django.contrib.gis.measure import D
                from django.contrib.gis.db.models.functions import Distance

                # Test a simple spatial query
                pnt = Point(0, 0, srid=4326)
                qs = Laundry.objects.filter(
                    location__distance_lte=(pnt, D(km=10))
                ).annotate(test_dist=Distance("location", pnt))
                if qs.exists():
                    obj = qs.first()
                    dist = getattr(obj, "test_dist", None)
                    try:
                        from rest_framework.renderers import JSONRenderer

                        JSONRenderer().render({"d": dist})
                        spatial_search_status = f"OK (Found {
                            qs.count()}, Serialized: {
                            type(dist)})"
                    except Exception as e:
                        spatial_search_status = f"Serialization Error: {
                            str(e)}"
                else:
                    spatial_search_status = (
                        "OK (No data in 10km radius, test query passed)"
                    )
            except Exception as e:
                import traceback

                spatial_search_status = f"Error: {
                    str(e)} | Details: {
                    traceback.format_exc().splitlines()[
                        -1]}"

        return Response(
            {
                "success": True,
                "diagnosis_version": "v1.8-SpatialFix",
                "message": "Use POST to trigger migrations and Location sync.",
                "data": {
                    "db_connection": db_conn,
                    "postgis": postgis_available,
                    "use_postgis_setting": os.getenv("USE_POSTGIS", "False"),
                    "debug_mode": settings.DEBUG,
                    "pending_migrations": pending_migrations,
                    "model_health": {
                        "Laundry": check_model_deep(Laundry),
                        "Service": check_model_deep(LaundryService),
                        "Review": check_model_deep(Review),
                        "Favorite": check_model_deep(Favorite),
                        "Order": check_model_deep(Order),
                        "SpecialOffer": check_model_deep(SpecialOffer),
                        "Notification": check_model_deep(Notification),
                        "SpatialSearchTest": spatial_search_status,
                    },
                    "allowed_hosts": settings.ALLOWED_HOSTS,
                },
            }
        )

    def post(self, request):
        """
        Trigger python manage.py migrate and sync Laundry locations.
        """
        out = io.StringIO()
        try:
            # 1. Run Migrations
            call_command("migrate", interactive=False, stdout=out)
            migration_result = out.getvalue()

            # 2. Sync Locations
            sync_count = 0
            if os.getenv("USE_POSTGIS", "False") == "True":
                from laundries.models.laundry import Laundry
                from django.contrib.gis.geos import Point

                laundries_to_sync = Laundry.objects.filter(location__isnull=True)
                for laundry_item in laundries_to_sync:
                    if laundry_item.latitude and laundry_item.longitude:
                        laundry_item.location = Point(
                            float(laundry_item.longitude),
                            float(laundry_item.latitude),
                            srid=4326,
                        )
                        laundry_item.save()
                        sync_count += 1

            return Response(
                {
                    "success": True,
                    "message": f"Tasks completed. Synced {sync_count} laundries.",
                    "output": migration_result,
                }
            )
        except Exception as e:
            return Response(
                {
                    "success": False,
                    "message": f"Tasks failed: {str(e)}",
                    "output": out.getvalue(),
                },
                status=500,
            )
