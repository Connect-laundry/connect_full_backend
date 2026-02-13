# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter
from .views import DeliveryAssignmentViewSet, TrackingViewSet

router = DefaultRouter()
router.register(r'assignments', DeliveryAssignmentViewSet, basename='assignment')
router.register(r'tracking', TrackingViewSet, basename='tracking')

urlpatterns = [
    path('', include(router.urls)),
]
