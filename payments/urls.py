from django.urls import path
from .views import PaymentInitializeView, PaymentVerifyView

urlpatterns = [
    path('initialize/', PaymentInitializeView.as_view(), name='payment_initialize'),
    path('verify/<str:reference>/', PaymentVerifyView.as_view(), name='payment_verify'),
]
