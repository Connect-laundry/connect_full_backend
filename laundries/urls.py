# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter
# pyre-ignore[missing-module]
from .views.laundry import LaundryViewSet, CategoryViewSet
# pyre-ignore[missing-module]
from .views.favorite import FavoriteListView
# pyre-ignore[missing-module]
from .views.review import ReviewCreateView
from .views.dashboard import (
    DashboardStatsView,
    DashboardEarningsView,
    DashboardOrderViewSet,
    ServiceStatusUpdateView
# pyre-ignore[missing-module]
)
from .views.my_laundry import MyLaundryView, MyLaundryDetailView
from .views.pricing import PricingItemViewSet, WeightPricingView
from .views.price_import import PriceImportViewSet
from .views.location import GeocodeView, HoursTemplateView
# pyre-ignore[missing-module]
from .views.admin_views import AdminLaundryViewSet, AdminServiceViewSet

router = DefaultRouter()
router.register(r'dashboard/orders', DashboardOrderViewSet, basename='dashboard-orders')
router.register(r'dashboard/pricing-items', PricingItemViewSet, basename='dashboard-pricing-items')
router.register(r'dashboard/price-imports', PriceImportViewSet, basename='dashboard-price-imports')
router.register(r'laundries', LaundryViewSet, basename='laundry')
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'admin/laundries', AdminLaundryViewSet, basename='admin-laundry')
router.register(r'admin/services', AdminServiceViewSet, basename='admin-service')

urlpatterns = [
    path('featured/', LaundryViewSet.as_view({'get': 'featured'}), name='laundry-featured-top'),
    path('dashboard/stats/', DashboardStatsView.as_view(), name='dashboard-stats'),
    path('dashboard/earnings/', DashboardEarningsView.as_view(), name='dashboard-earnings'),
    path('dashboard/my-laundry/', MyLaundryView.as_view(), name='dashboard-my-laundry'),
    path('dashboard/my-laundry/<uuid:id>/', MyLaundryDetailView.as_view(), name='dashboard-my-laundry-detail'),
    path('dashboard/my-laundry/hours/template/', HoursTemplateView.as_view(), name='dashboard-hours-template'),
    path('dashboard/weight-pricing/', WeightPricingView.as_view(), name='dashboard-weight-pricing'),
    path('dashboard/geocode/', GeocodeView.as_view(), name='dashboard-geocode'),
    path('dashboard/services/<uuid:id>/', ServiceStatusUpdateView.as_view(), name='dashboard-service-update'),
    path('favorites/', FavoriteListView.as_view(), name='favorite_list'),
    path('<uuid:laundry_id>/reviews/', ReviewCreateView.as_view(), name='review_create'),
    path('', include(router.urls)),
]

if settings.DEBUG:
    from .views.diagnosis import DiagnosisView

    urlpatterns.insert(1, path('diagnosis/', DiagnosisView.as_view(), name='diagnosis'))
