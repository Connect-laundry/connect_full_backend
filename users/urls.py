# pyre-ignore[missing-module]
from django.urls import path, include
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

# pyre-ignore[missing-module]
from .views.profile import ProfileView, AddressViewSet, LogoutView
# pyre-ignore[missing-module]
from .views.password_reset import PasswordResetRequestView, PasswordResetConfirmView
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'addresses', AddressViewSet, basename='address')

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='auth_register'),
    path('auth/login/', LoginView.as_view(), name='auth_login'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),
    path('auth/resend-otp/', ResendOTPView.as_view(), name='resend_otp'),
    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('auth/password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('auth/profile/', ProfileView.as_view(), name='auth_profile'),
    path('', include(router.urls)),
]
