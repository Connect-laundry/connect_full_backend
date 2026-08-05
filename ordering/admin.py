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
    fields = ('name', 'quantity', 'price')

    def has_add_permission(self, request, obj=None):
        # New priced lines are only ever added to a pay-after quote that has not
        # been invoiced yet. Every other order is priced at creation, and adding
        # rows to it would desync the items from the frozen total.
        if obj is None:
            return True
        return (
            obj.pricing_mode == Order.PricingMode.CUSTOM_QUOTE
            and obj.priced_at is None
        )

    def get_readonly_fields(self, request, obj=None):
        # Editable only while quoting an unpriced pay-after order; locked
        # everywhere else so a settled order's line items cannot be rewritten.
        if obj and obj.pricing_mode == Order.PricingMode.CUSTOM_QUOTE and obj.priced_at is None:
            return ()
        return ('name', 'quantity', 'price')


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
        'pricing_mode', 'estimated_weight_kg',
    )
    list_filter_sheet = True
    actions = ['send_quote']

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

    @admin.action(description="Send quote for selected pay-after orders")
    def send_quote(self, request, queryset):
        """
        Turn a priced pay-after order into a payable invoice.

        The operator adds the priced line items on the order's edit page (weight
        and inspection done), then runs this to freeze the total and tell the
        customer their invoice is ready. This is the platform-side bridge until
        the owner web app grows its own "Send quote" screen; the same effect is
        available to owners over POST /booking/lifecycle/{id}/quote/.
        """
        from django.contrib import messages
        from django.db import transaction
        from .services.finance_service import FinanceService
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification

        sent = skipped = 0
        for order in queryset:
            if order.pricing_mode != Order.PricingMode.CUSTOM_QUOTE or order.priced_at is not None:
                skipped += 1
                continue
            if not order.items.exists():
                skipped += 1
                continue

            with transaction.atomic():
                FinanceService.freeze_price_breakdown(order, coupon=order.coupon)

            NotificationService.notify_user(
                user=order.user,
                title="Your invoice is ready",
                body=f"Your laundry has been quoted GHS {order.total_amount} for order {order.order_no}. Tap to review and pay.",
                type=Notification.Type.ORDER,
                category="QUOTE_READY",
                related_order=order,
                dedup_key=f"quote_ready_{order.id}",
            )
            sent += 1

        self.message_user(
            request,
            f"Sent {sent} quote(s). Skipped {skipped} (not an unpriced pay-after order, or no items added).",
            level=messages.SUCCESS if sent else messages.WARNING,
        )

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
