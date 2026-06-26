# pyre-ignore[missing-module]
from django.contrib import admin

try:
    from unfold.admin import ModelAdmin as BaseModelAdmin
except Exception:  # pragma: no cover - unfold always present in this project
    BaseModelAdmin = admin.ModelAdmin

from .models import AnalyticsEvent


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(BaseModelAdmin):
    list_display = ('event_name', 'user', 'platform', 'screen_name', 'session_id', 'created_at')
    list_filter = ('event_name', 'platform', 'created_at')
    search_fields = ('event_name', 'user__email', 'session_id', 'screen_name')
    readonly_fields = (
        'id', 'event_name', 'user', 'session_id', 'device_id', 'platform',
        'os_version', 'app_version', 'screen_name', 'event_data', 'occurred_at',
        'created_at',
    )
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')
