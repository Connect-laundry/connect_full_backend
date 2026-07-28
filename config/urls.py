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
from django.views.generic import RedirectView, TemplateView
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from config.views.health import health_check
# pyre-ignore[missing-module]
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from marketplace.views.legal import PublicLegalHtmlView
from config.admin_analytics import analytics_dashboard_view, analytics_export_view
from config.insights import insights_view
from marketplace.campaign_center import (
    campaign_audience_view,
    campaign_compose_view,
    campaign_detail_view,
    campaign_list_view,
    campaign_schedule_view,
    campaign_send_view,
    campaign_test_view,
)

root_target = '/api/docs/' if settings.DEBUG else '/health/'


urlpatterns = [
    path('', RedirectView.as_view(url=root_target, permanent=False), name='root'),
    path('health/', health_check, name='health_check'),
    path('dashboard/', RedirectView.as_view(url='/admin/', permanent=False), name='dashboard_redirect'),
    path('manifest.webmanifest', TemplateView.as_view(
        template_name='pwa/manifest.html',
        content_type='application/manifest+json'
    ), name='pwa_manifest'),
    path('service-worker.js', TemplateView.as_view(
        template_name='pwa/service-worker.js',
        content_type='application/javascript',
        extra_context={'pwa_version': settings.PWA_VERSION}
    ), name='pwa_service_worker'),
    path('offline/', TemplateView.as_view(
        template_name='pwa/offline.html',
        content_type='text/html'
    ), name='pwa_offline'),
    path('legal/<slug:slug>/', PublicLegalHtmlView.as_view(), name='public_legal_page'),
    path('admin/insights/', insights_view, name='insights-home'),
    path('admin/insights/<str:section>/', insights_view, name='insights-section'),
    # Campaign Center — must precede admin.site.urls so /admin/campaigns/ is ours.
    path('admin/campaigns/', campaign_list_view, name='campaign-center'),
    path('admin/campaigns/new/', campaign_compose_view, name='campaign-center-new'),
    path('admin/campaigns/audience/', campaign_audience_view, name='campaign-center-audience'),
    path('admin/campaigns/<uuid:pk>/', campaign_detail_view, name='campaign-center-detail'),
    path('admin/campaigns/<uuid:pk>/edit/', campaign_compose_view, name='campaign-center-edit'),
    path('admin/campaigns/<uuid:pk>/send/', campaign_send_view, name='campaign-center-send'),
    path('admin/campaigns/<uuid:pk>/schedule/', campaign_schedule_view, name='campaign-center-schedule'),
    path('admin/campaigns/<uuid:pk>/test/', campaign_test_view, name='campaign-center-test'),
    path('admin/analytics-dashboard/', analytics_dashboard_view, name='admin-analytics-dashboard'),
    path('admin/analytics-export/', analytics_export_view, name='admin-analytics-export'),
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
    path('api/v1/analytics/', include('analytics.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

handler500 = 'django.views.defaults.server_error'

# Serve media files in development
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
