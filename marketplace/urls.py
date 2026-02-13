from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import FAQView, FeedbackView, NotificationViewSet, AdminMonitoringViewSet

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'admin/monitoring', AdminMonitoringViewSet, basename='admin-monitoring')

urlpatterns = [
    path('help/', FAQView.as_view(), name='faq'),
    path('feedback/', FeedbackView.as_view(), name='feedback'),
    path('', include(router.urls)),
]
