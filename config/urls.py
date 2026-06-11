"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# pyre-ignore[missing-module]
from django.contrib import admin
# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from django.views.generic import RedirectView
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from config.views.health import health_check
# pyre-ignore[missing-module]
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from marketplace.views.legal import PublicLegalHtmlView

root_target = '/api/schema/swagger-ui/' if settings.DEBUG else '/health/'

urlpatterns = [
    path('', RedirectView.as_view(url=root_target, permanent=False), name='root'),
    path('health/', health_check, name='health_check'),
    path('legal/<slug:slug>/', PublicLegalHtmlView.as_view(), name='public_legal_page'),
    path('admin/', admin.site.urls),
    path('api/v1/', include('users.urls')),
    path('api/v1/admin/', include('marketplace.admin_urls')),
    path('api/v1/legal/', include('marketplace.legal_urls')),
    path('api/v1/support/', include('marketplace.urls')),
    path('api/v1/laundries/', include('laundries.urls')),
    path('api/v1/booking/', include('ordering.urls')),
    path('api/v1/orders/', include('ordering.urls')),
    path('api/v1/logistics/', include('logistics.urls')),
    path('api/v1/payments/', include('payments.urls')),
]

if settings.DEBUG:
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    ]

# Serve media files in development
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
