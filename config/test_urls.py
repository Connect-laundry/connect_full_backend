from django.urls import include, path
from marketplace.views.legal import PublicLegalHtmlView


urlpatterns = [
    path('legal/<slug:slug>/', PublicLegalHtmlView.as_view(), name='public_legal_page'),
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

