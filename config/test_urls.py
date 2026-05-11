from django.urls import include, path


urlpatterns = [
    path('api/v1/', include('users.urls')),
    path('api/v1/support/', include('marketplace.urls')),
    path('api/v1/laundries/', include('laundries.urls')),
    path('api/v1/booking/', include('ordering.urls')),
    path('api/v1/orders/', include('ordering.urls')),
    path('api/v1/logistics/', include('logistics.urls')),
    path('api/v1/payments/', include('payments.urls')),
]
