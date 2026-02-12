# pyre-ignore[missing-module]
from django.urls import path
# pyre-ignore[missing-module]
from rest_framework_simplejwt.views import TokenRefreshView
# pyre-ignore[missing-module]
from .views.register import RegisterView
# pyre-ignore[missing-module]
from .views.login import LoginView
# pyre-ignore[missing-module]
from .views.verify_otp import VerifyOTPView
# pyre-ignore[missing-module]
from .views.resend_otp import ResendOTPView

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='auth_register'),
    path('auth/login/', LoginView.as_view(), name='auth_login'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),
    path('auth/resend-otp/', ResendOTPView.as_view(), name='resend_otp'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
