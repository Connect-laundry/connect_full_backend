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


from .models import LogisticsPricingConfig, LogisticsPricingAudit


@admin.register(LogisticsPricingConfig)
class LogisticsPricingConfigAdmin(ModelAdmin):
    list_display = (
        'version_display',
        'display_pricing_enabled',
        'display_pickup_rate',
        'display_delivery_rate',
        'pickup_base_fee',
        'delivery_base_fee',
        'max_service_distance_km',
        'effective_from',
        'is_active',
    )
    list_filter = ('pricing_enabled', 'is_active', 'effective_from')
    readonly_fields = ('id', 'version', 'currency', 'created_at', 'updated_at')

    fieldsets = (
        ("Master Toggle & Activation", {
            "fields": (
                'pricing_enabled',
                'is_active',
                'effective_from',
            ),
            "description": "When Pricing Enabled is OFF, mobile app displays that pickup and delivery are separate. When ON, live rates apply immediately.",
        }),
        ("Kilometre Pricing (GHS)", {
            "fields": (
                'pickup_price_per_km',
                'delivery_price_per_km',
            ),
            "description": "Authoritative price per kilometre. Backend computes distance between laundry and customer coordinates.",
        }),
        ("Base Fees & Minimums (GHS)", {
            "fields": (
                'pickup_base_fee',
                'delivery_base_fee',
                'pickup_min_fee',
                'delivery_min_fee',
            ),
        }),
        ("Service Constraints", {
            "fields": (
                'max_service_distance_km',
                'distance_rounding_precision',
            ),
        }),
        ("System & Audit", {
            "fields": (
                'id',
                'version',
                'currency',
                'created_at',
                'updated_at',
            ),
            "classes": ("collapse",),
        }),
    )

    @display(description="Version")
    def version_display(self, obj):
        return f"v{obj.version}"

    @display(description="Pricing Status", label={
        True: "success",
        False: "warning",
    })
    def display_pricing_enabled(self, obj):
        return obj.pricing_enabled

    @display(description="Pickup / km")
    def display_pickup_rate(self, obj):
        return f"GHS {obj.pickup_price_per_km}"

    @display(description="Delivery / km")
    def display_delivery_rate(self, obj):
        return f"GHS {obj.delivery_price_per_km}"

    def save_model(self, request, obj, form, change):
        old_data = {}
        new_data = {}
        if change:
            # Capture audit diff
            tracked_fields = [
                'pricing_enabled', 'pickup_price_per_km', 'delivery_price_per_km',
                'pickup_base_fee', 'delivery_base_fee', 'pickup_min_fee', 'delivery_min_fee',
                'max_service_distance_km', 'distance_rounding_precision', 'is_active', 'effective_from'
            ]
            for f in tracked_fields:
                old_val = form.initial.get(f)
                new_val = form.cleaned_data.get(f)
                if str(old_val) != str(new_val):
                    old_data[f] = str(old_val)
                    new_data[f] = str(new_val)
            if old_data:
                obj.version = obj.version + 1
        super().save_model(request, obj, form, change)

        # Record audit log
        LogisticsPricingAudit.objects.create(
            config=obj,
            changed_by=request.user if request.user.is_authenticated else None,
            old_values=old_data,
            new_values=new_data or {k: str(form.cleaned_data.get(k)) for k in form.cleaned_data if k in form.fields},
            notes="Modified via Django Admin" if change else "Created via Django Admin"
        )


@admin.register(LogisticsPricingAudit)
class LogisticsPricingAuditAdmin(ModelAdmin):
    list_display = ('config_version', 'changed_by', 'changed_at', 'notes')
    list_filter = ('changed_at',)
    search_fields = ('changed_by__email', 'notes')
    readonly_fields = ('id', 'config', 'changed_by', 'changed_at', 'old_values', 'new_values', 'notes')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('config', 'changed_by')

    @display(description="Config Version")
    def config_version(self, obj):
        return f"v{obj.config.version} ({obj.config.id})"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

