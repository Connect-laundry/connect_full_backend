# pyre-ignore[missing-module]
from django.urls import path
# pyre-ignore[missing-module]
from .views import PaymentInitializeView, PaymentVerifyView
from .webhooks import paystack_webhook
from .payout_views import BankAccountView, PayoutRequestView, PayoutHistoryView

urlpatterns = [
    path('initialize/', PaymentInitializeView.as_view(), name='payment_initialize'),
    path('verify/<str:reference>/', PaymentVerifyView.as_view(), name='payment_verify'),
    path('webhook/', paystack_webhook, name='paystack_webhook'),
    # Payout Controls
    path('payouts/bank-account/', BankAccountView.as_view(), name='payout_bank_account'),
    path('payouts/request/', PayoutRequestView.as_view(), name='payout_request'),
    path('payouts/history/', PayoutHistoryView.as_view(), name='payout_history'),
]

