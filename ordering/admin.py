from django.contrib import admin 
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display
from .models.base import LaunderableItem, Order, OrderItem
from .models.coupons import Coupon, CouponUsage
from laundries.models import Category, LaundryService
from django.utils.html import format_html


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('item', 'quantity', 'price')


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = (
        'display_order_no',
        'user',
        'display_customer_phone',
        'laundry',
        'display_status',
        'total_amount',
        'created_at'
    )
    list_filter = ('status', 'created_at')
    search_fields = ('order_no', 'user__email', 'user__phone', 'laundry__name')
    inlines = [OrderItemInline]
    readonly_fields = (
        'order_no', 'created_at', 'updated_at',
        'display_customer_phone', 'display_pickup_address',
    )
    list_filter_sheet = True

    fieldsets = (
        ('Order info', {
            'fields': ('order_no', 'user', 'laundry', 'status', 'total_amount')
        }),
        ('Customer contact & pickup', {
            'fields': ('display_customer_phone', 'display_pickup_address'),
            'description': 'Reach the customer and locate the pickup for this order.',
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'laundry')

    @display(description="Order #", ordering="order_no")
    def display_order_no(self, obj):
        order_no = getattr(obj, 'order_no', '—') if obj else '—'
        return format_html('<span class="font-mono font-bold text-primary-600">{}</span>', order_no)

    @display(description="Customer phone", ordering="user__phone")
    def display_customer_phone(self, obj):
        user = getattr(obj, 'user', None) if obj else None
        phone = getattr(user, 'phone', None) if user else None
        if not phone:
            return format_html('<span class="text-red-500">{}</span>', 'Not provided')
        return format_html('<a href="tel:{}" class="font-mono text-primary-600">{}</a>', phone, phone)

    @display(description="Pickup address")
    def display_pickup_address(self, obj):
        if not obj:
            return '—'
        return getattr(obj, 'pickup_address', None) or getattr(obj, 'address', None) or '—'

    @display(description="Status", label={
        "PENDING": "warning",
        "CONFIRMED": "info",
        "REJECTED": "danger",
        "PICKED_UP": "info",
        "IN_PROCESS": "info",
        "OUT_FOR_DELIVERY": "info",
        "DELIVERED": "success",
        "COMPLETED": "success",
        "CANCELLED": "danger",
    })
    def display_status(self, obj):
        return getattr(obj, 'status', 'PENDING') if obj else 'PENDING'


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(LaundryService)
class LaundryServiceAdmin(ModelAdmin):
    list_display = ('item', 'service_type', 'price', 'laundry')
    list_filter = ('service_type', 'laundry')
    search_fields = ('item__name', 'laundry__name')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('laundry', 'item')


class CouponUsageInline(TabularInline):
    """Redemptions, shown read-only on the coupon page."""
    model = CouponUsage
    extra = 0
    can_delete = False
    readonly_fields = ('user', 'order', 'used_at')
    fields = ('user', 'order', 'used_at')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Coupon)
class CouponAdmin(ModelAdmin):
    """Discount codes.

    Previously unmanageable outside a database shell: the API validated and
    redeemed coupons, but nothing could create one.
    """
    list_display = (
        'code',
        'display_discount',
        'display_usage',
        'valid_from',
        'valid_to',
        'display_active',
    )
    list_filter = ('is_active', 'discount_type', 'first_time_users_only', 'valid_from')
    search_fields = ('code',)
    readonly_fields = ('current_usage', 'created_at', 'updated_at')
    filter_horizontal = ('applicable_laundries',)
    inlines = [CouponUsageInline]

    fieldsets = (
        ('Code', {
            'fields': ('code', 'is_active'),
        }),
        ('Discount', {
            'fields': ('discount_type', 'discount_value', 'max_discount_amount', 'min_order_value'),
            'description': 'PERCENTAGE uses discount_value as a percent; cap it with max discount.',
        }),
        ('Validity window', {
            'fields': ('valid_from', 'valid_to'),
        }),
        ('Limits', {
            'fields': ('max_usage', 'current_usage', 'user_limit', 'first_time_users_only'),
        }),
        ('Scope', {
            'fields': ('applicable_laundries',),
            'description': 'Leave empty to apply to every laundry.',
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @display(description='Discount')
    def display_discount(self, obj):
        if obj.discount_type == 'PERCENTAGE':
            cap = f" (max {obj.max_discount_amount})" if obj.max_discount_amount else ''
            return f"{obj.discount_value}%{cap}"
        return f"GHS {obj.discount_value}"

    @display(description='Used')
    def display_usage(self, obj):
        if obj.max_usage:
            return f"{obj.current_usage} / {obj.max_usage}"
        return f"{obj.current_usage} / ∞"

    @display(description='Active', boolean=True)
    def display_active(self, obj):
        return obj.is_active


@admin.register(CouponUsage)
class CouponUsageAdmin(ModelAdmin):
    list_display = ('coupon', 'user', 'order', 'used_at')
    list_filter = ('used_at',)
    search_fields = ('coupon__code', 'user__email', 'order__order_no')
    readonly_fields = ('coupon', 'user', 'order', 'used_at')

    def has_add_permission(self, request):
        # Redemptions are created by the booking flow, never by hand.
        return False


@admin.register(LaunderableItem)
class LaunderableItemAdmin(ModelAdmin):
    """The global item catalog every LaundryService points at."""
    list_display = ('name', 'item_category', 'is_active', 'created_at')
    list_filter = ('is_active', 'item_category')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')
