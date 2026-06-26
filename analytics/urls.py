from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import AnalyticsIngestView, AnalyticsSummaryView
from .dashboards import DashboardViewSet

router = DefaultRouter()
router.register(r'dashboards', DashboardViewSet, basename='dashboard')

urlpatterns = [
    path('events/', AnalyticsIngestView.as_view(), name='analytics-ingest'),
    path('summary/', AnalyticsSummaryView.as_view(), name='analytics-summary'),
    path('', include(router.urls)),
]
