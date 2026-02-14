from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.faq import FAQView
from .views.feedback import FeedbackView
from .views.notifications import NotificationViewSet

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')

urlpatterns = [
    path('help/faq/', FAQView.as_view(), name='faq'),
    path('help/feedback/', FeedbackView.as_view(), name='feedback'),
    path('', include(router.urls)),
]
