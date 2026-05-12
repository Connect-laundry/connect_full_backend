from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import DeliveryAssignment, TrackingLog


@admin.register(DeliveryAssignment)
class DeliveryAssignmentAdmin(ModelAdmin):
    list_display = (
        'order',
        'driver',
        'assignment_type',
        'display_status',
        'assigned_at',
        'completed_at',
    )
    list_filter = ('assignment_type', 'status', 'assigned_at')
    search_fields = ('order__order_no', 'driver__email', 'driver__first_name')
    list_filter_sheet = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('order', 'driver')

    @display(description="Status", label={
        "ASSIGNED": "warning",
        "IN_TRANSIT": "info",
        "COMPLETED": "success",
    })
    def display_status(self, obj):
        return obj.status


@admin.register(TrackingLog)
class TrackingLogAdmin(ModelAdmin):
    list_display = ('order', 'status', 'location_name', 'timestamp')
    list_filter = ('status', 'timestamp')
    search_fields = ('order__order_no', 'description', 'location_name')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('order')
