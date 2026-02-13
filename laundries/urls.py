# pyre-ignore[missing-module]
from django.urls import path, include
# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter
# pyre-ignore[missing-module]
from rest_framework import permissions
# pyre-ignore[missing-module]
from .views.laundry import LaundryViewSet
# pyre-ignore[missing-module]
from .views.favorite import FavoriteListView
# pyre-ignore[missing-module]
from .views.review import ReviewCreateView
# pyre-ignore[missing-module]
from .views.dashboard import LaundryDashboardViewSet
from .views.admin_views import AdminLaundryViewSet, AdminServiceViewSet

router = DefaultRouter()
router.register(r'dashboard', LaundryDashboardViewSet, basename='dashboard')
router.register(r'laundries', LaundryViewSet, basename='laundry')
router.register(r'admin/laundries', AdminLaundryViewSet, basename='admin-laundry')
router.register(r'admin/services', AdminServiceViewSet, basename='admin-service')

urlpatterns = [
    path('favorites/', FavoriteListView.as_view(), name='favorite_list'),
    path('<uuid:laundry_id>/reviews/', ReviewCreateView.as_view(), name='review_create'),
    path('', include(router.urls)),
]
