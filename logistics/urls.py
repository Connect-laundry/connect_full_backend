# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter
from .views import DeliveryAssignmentViewSet, TrackingViewSet, LogisticsPricingView, LogisticsQuoteView

router = DefaultRouter()
router.register(r'assignments', DeliveryAssignmentViewSet, basename='assignment')
router.register(r'tracking', TrackingViewSet, basename='tracking')

urlpatterns = [
    path('pricing/', LogisticsPricingView.as_view(), name='logistics-pricing'),
    path('quote/', LogisticsQuoteView.as_view(), name='logistics-quote'),
    path('', include(router.urls)),
]
