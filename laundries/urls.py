from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.laundry import LaundryViewSet
from .views.favorite import FavoriteListView
from .views.review import ReviewCreateView
from .views.dashboard import (
    DashboardStatsView,
    DashboardEarningsView,
    DashboardOrderViewSet,
    ServiceStatusUpdateView
)
from .views.admin_views import AdminLaundryViewSet, AdminServiceViewSet

router = DefaultRouter()
router.register(r'dashboard/orders', DashboardOrderViewSet, basename='dashboard-orders')
router.register(r'laundries', LaundryViewSet, basename='laundry')
router.register(r'admin/laundries', AdminLaundryViewSet, basename='admin-laundry')
router.register(r'admin/services', AdminServiceViewSet, basename='admin-service')

urlpatterns = [
    path('dashboard/stats/', DashboardStatsView.as_view(), name='dashboard-stats'),
    path('dashboard/earnings/', DashboardEarningsView.as_view(), name='dashboard-earnings'),
    path('dashboard/services/<uuid:id>/', ServiceStatusUpdateView.as_view(), name='dashboard-service-update'),
    path('favorites/', FavoriteListView.as_view(), name='favorite_list'),
    path('<uuid:laundry_id>/reviews/', ReviewCreateView.as_view(), name='review_create'),
    path('', include(router.urls)),
]
