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


from django.db.models import Count, Q, Sum

from .models import LogisticsPricingConfig, LogisticsPricingAudit

AUDITED_FIELDS = (
    'pricing_enabled', 'pickup_price_per_km', 'delivery_price_per_km',
    'pickup_base_fee', 'delivery_base_fee', 'pickup_min_fee', 'delivery_min_fee',
    'max_service_distance_km', 'distance_rounding_precision',
    'minimum_billable_distance_km', 'road_distance_factor', 'is_active', 'effective_from',
    'min_android_build', 'min_ios_build',
)


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
    readonly_fields = (
        'id', 'version', 'currency', 'created_at', 'updated_at',
        'orders_priced', 'rider_transport_total', 'customer_transport_total', 'promo_subsidy_totals',
    )

    fieldsets = (
        ("Master switch", {
            "fields": (
                'pricing_enabled',
                'is_active',
                'effective_from',
            ),
            "description": (
                "Pricing enabled OFF: the app tells customers pickup and delivery are not included and "
                "confirmed separately. ON: every quote uses the rates below, in all installed apps, "
                "within a few seconds. No app update is needed. Booked orders keep the rates they were "
                "priced with."
            ),
        }),
        ("Price per kilometre (GHS)", {
            "fields": (
                'pickup_price_per_km',
                'delivery_price_per_km',
            ),
            "description": (
                "Pickup = customer pickup pin to laundry. Delivery = laundry to customer delivery pin. "
                "Fee per leg = base fee + (km x rate), never below the minimum."
            ),
        }),
        ("Base fees and minimums (GHS)", {
            "fields": (
                'pickup_base_fee',
                'delivery_base_fee',
                'pickup_min_fee',
                'delivery_min_fee',
            ),
        }),
        ("Distance rules", {
            "fields": (
                'max_service_distance_km',
                'minimum_billable_distance_km',
                'road_distance_factor',
                'distance_rounding_precision',
            ),
            "description": (
                "Distance is measured between the map pins (metres internally) and billed in km. "
                "Trips beyond the maximum distance cannot be booked."
            ),
        }),
        ("Minimum app build while pricing is ON", {
            "fields": (
                'min_android_build',
                'min_ios_build',
            ),
            "description": (
                "Older app builds describe transport as not charged in the app, so while pricing is ON "
                "they cannot start or place a booking and are asked to update. Viewing orders, tracking, "
                "delivery confirmation, disputes and support keep working. Build 9 is the launch build; "
                "set these to the first build that ships the new transport screens."
            ),
        }),
        ("Usage (orders priced with this version)", {
            "fields": (
                'orders_priced',
                'rider_transport_total',
                'customer_transport_total',
                'promo_subsidy_totals',
            ),
        }),
        ("System", {
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

    def _usage(self, obj):
        if not obj or not obj.pk:
            return {}
        cached = getattr(obj, '_usage_cache', None)
        if cached is None:
            from ordering.models import Order
            cached = Order.objects.filter(logistics_pricing_version=f"v{obj.version}").aggregate(
                orders=Count('id'),
                rider=Sum('logistics_nominal_total'),
                pickup=Sum('pickup_fee'),
                delivery=Sum('delivery_fee'),
                laundry_funded=Sum('logistics_discount', filter=Q(is_free_delivery_promo=True, promo_funding_source='LAUNDRY')),
                simame_funded=Sum('logistics_discount', filter=Q(is_free_delivery_promo=True, promo_funding_source='SIMAME')),
            )
            obj._usage_cache = cached
        return cached

    @display(description="Orders priced")
    def orders_priced(self, obj):
        return self._usage(obj).get('orders') or 0

    @display(description="Rider transport total (GHS)")
    def rider_transport_total(self, obj):
        return self._usage(obj).get('rider') or 0

    @display(description="Paid by customers (GHS)")
    def customer_transport_total(self, obj):
        usage = self._usage(obj)
        return (usage.get('pickup') or 0) + (usage.get('delivery') or 0)

    @display(description="Promo subsidies (GHS)")
    def promo_subsidy_totals(self, obj):
        usage = self._usage(obj)
        return f"Laundry funded: {usage.get('laundry_funded') or 0} | Simame funded: {usage.get('simame_funded') or 0}"

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
        old_data, new_data = {}, {}
        if change:
            for field in AUDITED_FIELDS:
                if field in form.changed_data:
                    old_data[field] = str(form.initial.get(field))
                    new_data[field] = str(form.cleaned_data.get(field))
            if new_data:
                # A new version number for every change, so an order's version
                # always identifies exactly the rates it was priced with.
                obj.version = LogisticsPricingConfig.next_version()
        else:
            new_data = {field: str(form.cleaned_data.get(field)) for field in AUDITED_FIELDS if field in form.cleaned_data}
        super().save_model(request, obj, form, change)

        if new_data or not change:
            LogisticsPricingAudit.objects.create(
                config=obj,
                changed_by=request.user if request.user.is_authenticated else None,
                old_values=old_data,
                new_values=new_data,
                notes=f"{'Changed' if change else 'Created'} via Django admin (now v{obj.version})",
            )

    def has_delete_permission(self, request, obj=None):
        # Orders reference versions by number; deleting a row would orphan them.
        return False


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

