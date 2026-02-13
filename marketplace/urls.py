# pyre-ignore[missing-module]
from django.urls import path
from .views import FAQView, FeedbackView

urlpatterns = [
    path('help/', FAQView.as_view(), name='faq'),
    path('feedback/', FeedbackView.as_view(), name='feedback'),
]
