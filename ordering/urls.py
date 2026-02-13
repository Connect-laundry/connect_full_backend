# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter
from .views import CatalogViewSet, BookingViewSet, OrderViewSet

router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='order')

urlpatterns = [
    # Catalog endpoints
    path('services/', CatalogViewSet.as_view({'get': 'list'}), name='catalog-services'),
    path('items/', CatalogViewSet.as_view({'get': 'items'}), name='catalog-items'),
    
    # Booking endpoints
    path('schedule/', BookingViewSet.as_view({'get': 'schedule'}), name='booking-schedule'),
    path('create/', BookingViewSet.as_view({'post': 'create'}), name='booking-create'),
    
    path('', include(router.urls)),
]
