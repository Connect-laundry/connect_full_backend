# pyre-ignore[missing-module]
from django.urls import path
# pyre-ignore[missing-module]
from .views import PaymentInitializeView, PaymentVerifyView
from .webhooks import paystack_webhook

urlpatterns = [
    path('initialize/', PaymentInitializeView.as_view(), name='payment_initialize'),
    path('verify/<str:reference>/', PaymentVerifyView.as_view(), name='payment_verify'),
    path('webhook/', paystack_webhook, name='paystack_webhook'),
]
