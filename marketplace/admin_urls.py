"""URL routes for the Admin Operations Center API (mounted at /api/v1/admin/)."""
from django.urls import path

from .views.admin_api import (
    AdminSearchView,
    AdminNotificationListView,
    AdminNotificationUnreadCountView,
    AdminNotificationMarkReadView,
    AdminNotificationMarkAllReadView,
    AdminNotificationPushDeviceView,
    AdminAuditLogView,
)

urlpatterns = [
    path('search/', AdminSearchView.as_view(), name='admin_search'),
    path('notifications/', AdminNotificationListView.as_view(), name='admin_notifications'),
    path('notifications/unread-count/', AdminNotificationUnreadCountView.as_view(), name='admin_notifications_unread'),
    path('notifications/<uuid:pk>/read/', AdminNotificationMarkReadView.as_view(), name='admin_notification_read'),
    path('notifications/read-all/', AdminNotificationMarkAllReadView.as_view(), name='admin_notifications_read_all'),
    path('notifications/push-device/', AdminNotificationPushDeviceView.as_view(), name='admin_notifications_push_device'),
    path('audit-log/', AdminAuditLogView.as_view(), name='admin_audit_log'),
]
