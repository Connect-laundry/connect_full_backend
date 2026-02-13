# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter
# pyre-ignore[missing-module]
from .views import CatalogViewSet, BookingViewSet, OrderViewSet
# pyre-ignore[missing-module]
from .views.lifecycle import OrderLifecycleViewSet
# pyre-ignore[missing-module]
from .views.promo import CouponViewSet

router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'lifecycle', OrderLifecycleViewSet, basename='order-lifecycle')
router.register(r'coupons', CouponViewSet, basename='coupon')

urlpatterns = [
    # Catalog endpoints
    path('services/', CatalogViewSet.as_view({'get': 'list'}), name='catalog-services'),
    path('items/', CatalogViewSet.as_view({'get': 'items'}), name='catalog-items'),
    
    # Booking endpoints
    path('schedule/', BookingViewSet.as_view({'get': 'schedule'}), name='booking-schedule'),
    path('create/', BookingViewSet.as_view({'post': 'create'}), name='booking-create'),
    
    path('', include(router.urls)),
]
