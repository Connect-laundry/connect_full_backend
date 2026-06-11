from django.urls import include, path
from django.views.generic import RedirectView, TemplateView
from django.conf import settings
from marketplace.views.legal import PublicLegalHtmlView

urlpatterns = [
    path('legal/<slug:slug>/', PublicLegalHtmlView.as_view(), name='public_legal_page'),
    path('dashboard/', RedirectView.as_view(url='/admin/', permanent=False), name='dashboard_redirect'),
    path('manifest.webmanifest', TemplateView.as_view(
        template_name='pwa/manifest.webmanifest',
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

