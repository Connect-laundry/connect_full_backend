from django.contrib import admin 
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display
from .models.base import Order, OrderItem
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
