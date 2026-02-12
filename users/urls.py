# pyre-ignore[missing-module]
from django.urls import path
# pyre-ignore[missing-module]
from rest_framework_simplejwt.views import TokenRefreshView
# pyre-ignore[missing-module]
from .views.register import RegisterView
# pyre-ignore[missing-module]
from .views.login import LoginView

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='auth_register'),
    path('auth/login/', LoginView.as_view(), name='auth_login'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
