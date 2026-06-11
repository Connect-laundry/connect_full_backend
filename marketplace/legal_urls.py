from django.urls import path

from .views.legal import (
    LegalAdminArchiveView,
    LegalAdminCreateView,
    LegalAdminDetailView,
    LegalAdminPublishView,
    LegalAdminRollbackView,
    LegalCurrentVersionsView,
    LegalDocumentDetailView,
    LegalDocumentListView,
    LegalUserAcceptanceView,
)

urlpatterns = [
    path('', LegalDocumentListView.as_view(), name='legal_list'),
    path('current-versions/', LegalCurrentVersionsView.as_view(), name='legal_current_versions'),
    path('user-acceptance/', LegalUserAcceptanceView.as_view(), name='legal_user_acceptance'),
    path('admin/create/', LegalAdminCreateView.as_view(), name='legal_admin_create'),
    path('admin/<uuid:pk>/', LegalAdminDetailView.as_view(), name='legal_admin_detail'),
    path('admin/publish/', LegalAdminPublishView.as_view(), name='legal_admin_publish'),
    path('admin/archive/', LegalAdminArchiveView.as_view(), name='legal_admin_archive'),
    path('admin/rollback/', LegalAdminRollbackView.as_view(), name='legal_admin_rollback'),
    path('<slug:slug>/', LegalDocumentDetailView.as_view(), name='legal_detail'),
]
