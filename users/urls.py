# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from .views.token_refresh import CustomTokenRefreshView
# pyre-ignore[missing-module]
from .views.profile import ProfileView, AddressViewSet, LogoutView, SupportedCitiesView
from .views.profile import DeleteAccountView
# pyre-ignore[missing-module]
from .views.login import LoginView
# pyre-ignore[missing-module]
from .views.register import RegisterView
# pyre-ignore[missing-module]
from .views.deactivate import UserDeactivateView
# pyre-ignore[missing-module]
from .views.referral import ReferralApplyView, ReferralStatsView
# pyre-ignore[missing-module]
from .views.media import MediaUploadView
# pyre-ignore[missing-module]
from .views.password_reset import ForgotPasswordView, ResetPasswordView
# pyre-ignore[missing-module]
from .views.sessions import ActiveSessionsView, RevokeCurrentSessionView, RevokeAllSessionsView
from .views.social import SessionView, SocialLoginView
from .views.clerk_webhook import ClerkWebhookView
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'addresses', AddressViewSet, basename='address')

urlpatterns = [
    # Auth
    path('auth/register/', RegisterView.as_view(), name='auth_register'),
    path('auth/login/', LoginView.as_view(), name='auth_login'),
    path('auth/social-login/', SocialLoginView.as_view(), name='auth_social_login'),
    path('auth/clerk/webhook/', ClerkWebhookView.as_view(), name='auth_clerk_webhook'),
    path('auth/session/', SessionView.as_view(), name='auth_session'),
    path('auth/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('auth/logout-all/', RevokeAllSessionsView.as_view(), name='auth_logout_all'),
    path('auth/me/', ProfileView.as_view(), name='auth_me'),
    path('auth/account/', DeleteAccountView.as_view(), name='auth_delete_account'),
    path('auth/sessions/', ActiveSessionsView.as_view(), name='auth_sessions'),
    path('auth/sessions/revoke-current/', RevokeCurrentSessionView.as_view(), name='auth_revoke_current_session'),
    path('auth/sessions/revoke-all/', RevokeAllSessionsView.as_view(), name='auth_revoke_all_sessions'),
    path('auth/forgot-password/', ForgotPasswordView.as_view(), name='auth_forgot_password'),
    path('auth/reset-password/', ResetPasswordView.as_view(), name='auth_reset_password'),
    path('addresses/supported-cities/', SupportedCitiesView.as_view(), name='supported_cities'),
    
    # User Actions
    path('users/<uuid:pk>/deactivate/', UserDeactivateView.as_view(), name='user_deactivate'),
    
    # Referrals
    path('referral/apply/', ReferralApplyView.as_view(), name='referral_apply'),
    path('referral/stats/', ReferralStatsView.as_view(), name='referral_stats'),
    
    # Media
    path('media/upload/', MediaUploadView.as_view(), name='media_upload'),
    
    path('', include(router.urls)),
]
