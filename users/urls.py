# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from rest_framework_simplejwt.views import TokenRefreshView
# pyre-ignore[missing-module]
from .views.profile import ProfileView, AddressViewSet, LogoutView
# pyre-ignore[missing-module]
from .views.deactivate import UserDeactivateView
# pyre-ignore[missing-module]
from .views.clerk_auth import VerifyClerkTokenView, ClerkMeView, ClerkLogoutView
# pyre-ignore[missing-module]
from .views.referral import ReferralApplyView, ReferralStatsView
# pyre-ignore[missing-module]
from .views.media import MediaUploadView
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'addresses', AddressViewSet, basename='address')

urlpatterns = [
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('auth/profile/', ProfileView.as_view(), name='auth_profile'),
    path('users/<uuid:pk>/deactivate/', UserDeactivateView.as_view(), name='user_deactivate'),
    path('auth/clerk/verify/', VerifyClerkTokenView.as_view(), name='clerk_token_verify'),
    path('auth/clerk/me/', ClerkMeView.as_view(), name='clerk_me'),
    path('auth/clerk/logout/', ClerkLogoutView.as_view(), name='clerk_logout'),
    
    # Referrals
    path('referral/apply/', ReferralApplyView.as_view(), name='referral_apply'),
    path('referral/stats/', ReferralStatsView.as_view(), name='referral_stats'),
    
    # Media
    path('media/upload/', MediaUploadView.as_view(), name='media_upload'),
    
    path('', include(router.urls)),
]
