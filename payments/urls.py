from django.urls import path
from .views import (
    PaymentInitializeView, 
    PaymentVerifyView, 
    PaymentStatusView, 
    PaymentReceiptView, 
    PaymentAnalyticsView, 
    PaymentOwnerStatsView
)
from .webhooks import paystack_webhook

urlpatterns = [
    path('initialize/', PaymentInitializeView.as_view(), name='payment_initialize'),
    path('verify/<str:reference>/', PaymentVerifyView.as_view(), name='payment_verify'),
    path('status/<str:reference>/', PaymentStatusView.as_view(), name='payment_status'),
    path('receipt/<str:reference>/', PaymentReceiptView.as_view(), name='payment_receipt'),
    path('analytics/', PaymentAnalyticsView.as_view(), name='payment_analytics'),
    path('owner-stats/', PaymentOwnerStatsView.as_view(), name='payment_owner_stats'),
    path('webhook/', paystack_webhook, name='paystack_webhook'),
    path('paystack/webhook/', paystack_webhook, name='paystack_webhook_alt'),
]
