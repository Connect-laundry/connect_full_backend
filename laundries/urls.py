# pyre-ignore[missing-module]
from django.urls import path, include

# pyre-ignore[missing-module]
from rest_framework.routers import DefaultRouter

# pyre-ignore[missing-module]
from .views.laundry import LaundryViewSet, CategoryViewSet
from .views.diagnosis import DiagnosisView

# pyre-ignore[missing-module]
from .views.favorite import FavoriteListView

# pyre-ignore[missing-module]
from .views.review import ReviewCreateView
from .views.dashboard import (
    DashboardStatsView,
    DashboardEarningsView,
    DashboardOrderViewSet,
    ServiceStatusUpdateView,
    # pyre-ignore[missing-module]
)

# pyre-ignore[missing-module]
from .views.admin_views import AdminLaundryViewSet, AdminServiceViewSet

# pyre-ignore[missing-module]
from .views.owner import OwnerLaundryViewSet
from .views.machine import MachineViewSet
from .views.staff import StaffViewSet
from .views.crm import CustomerListView, CustomerProfileView

router = DefaultRouter()
router.register(r"dashboard/orders", DashboardOrderViewSet, basename="dashboard-orders")
router.register(r"dashboard/my-laundry", OwnerLaundryViewSet, basename="owner-laundry")
router.register(r"dashboard/machines", MachineViewSet, basename="dashboard-machines")
router.register(r"dashboard/staff", StaffViewSet, basename="dashboard-staff")
router.register(r"laundries", LaundryViewSet, basename="laundry")
router.register(r"categories", CategoryViewSet, basename="category")
router.register(r"admin/laundries", AdminLaundryViewSet, basename="admin-laundry")
router.register(r"admin/services", AdminServiceViewSet, basename="admin-service")

urlpatterns = [
    path(
        "featured/",
        LaundryViewSet.as_view({"get": "featured"}),
        name="laundry-featured-top",
    ),
    path("diagnosis/", DiagnosisView.as_view(), name="diagnosis"),
    path("dashboard/stats/", DashboardStatsView.as_view(), name="dashboard-stats"),
    path(
        "dashboard/earnings/",
        DashboardEarningsView.as_view(),
        name="dashboard-earnings",
    ),
    path(
        "dashboard/services/<uuid:id>/",
        ServiceStatusUpdateView.as_view(),
        name="dashboard-service-update",
    ),
    path(
        "dashboard/customers/", CustomerListView.as_view(), name="dashboard-customers"
    ),
    path(
        "dashboard/customers/<uuid:user_id>/profile/",
        CustomerProfileView.as_view(),
        name="dashboard-customer-profile",
    ),
    path("favorites/", FavoriteListView.as_view(), name="favorite_list"),
    path(
        "<uuid:laundry_id>/reviews/", ReviewCreateView.as_view(), name="review_create"
    ),
    path("", include(router.urls)),
]
