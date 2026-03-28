# pyre-ignore[missing-module]
from django.contrib import admin 
# pyre-ignore[missing-module]
from .models.base import Order, OrderItem
# pyre-ignore[missing-module]
from laundries.models import Category, LaundryService

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('item', 'quantity', 'price')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'user', 'laundry', 'status', 'estimated_price', 'final_price', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('order_no', 'user__email', 'laundry__name')
    inlines = [OrderItemInline]
    readonly_fields = ('order_no', 'created_at', 'updated_at')
    
    fieldsets = (
        ('Order info', {
            'fields': ('order_no', 'user', 'laundry', 'status', 'estimated_price', 'final_price')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(LaundryService)
class LaundryServiceAdmin(admin.ModelAdmin):
    list_display = ('item', 'service_type', 'price', 'laundry')
    list_filter = ('service_type', 'laundry')
    search_fields = ('item__name', 'laundry__name')
