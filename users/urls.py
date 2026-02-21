# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from .views.token_refresh import CustomTokenRefreshView
# pyre-ignore[missing-module]
from .views.profile import ProfileView, AddressViewSet, LogoutView
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
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'addresses', AddressViewSet, basename='address')

urlpatterns = [
    # Auth
    path('auth/register/', RegisterView.as_view(), name='auth_register'),
    path('auth/login/', LoginView.as_view(), name='auth_login'),
    path('auth/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('auth/me/', ProfileView.as_view(), name='auth_me'),
    
    # User Actions
    path('users/<uuid:pk>/deactivate/', UserDeactivateView.as_view(), name='user_deactivate'),
    
    # Referrals
    path('referral/apply/', ReferralApplyView.as_view(), name='referral_apply'),
    path('referral/stats/', ReferralStatsView.as_view(), name='referral_stats'),
    
    # Media
    path('media/upload/', MediaUploadView.as_view(), name='media_upload'),
    
    path('', include(router.urls)),
]
