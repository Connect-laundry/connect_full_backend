from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.db import connection
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
            
        return Response({
            "status": "success",
            "data": {
                "db_connection": db_conn,
                "postgis": postgis_available,
                "use_postgis_setting": os.getenv('USE_POSTGIS', 'False'),
                "debug_mode": settings.DEBUG,
                "allowed_hosts": settings.ALLOWED_HOSTS,
            }
        })
