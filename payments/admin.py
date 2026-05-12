from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import Payment, WebhookEvent
from django.utils.html import format_html


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = (
        'transaction_reference',
        'order_link',
        'display_amount',
        'display_status',
        'payment_method',
        'paid_at',
    )
    list_filter = ('status', 'payment_method', 'created_at')
    search_fields = ('transaction_reference', 'paystack_reference', 'order__order_no', 'user__email')
    readonly_fields = ('transaction_reference', 'paystack_reference', 'raw_response', 'created_at', 'updated_at')
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('order', 'user')

    @display(description="Order")
    def order_link(self, obj):
        from django.urls import reverse
        url = reverse("admin:ordering_order_change", args=[obj.order.id])
        return format_html('<a href="{}" class="font-mono text-primary-600 underline">{}</a>', url, obj.order.order_no)

    @display(description="Amount", ordering="amount")
    def display_amount(self, obj):
        return f"{obj.amount} {obj.currency}"

    @display(description="Status", label={
        "SUCCESS": "success",
        "PENDING": "warning",
        "FAILED": "danger",
        "EXPIRED": "warning",
    })
    def display_status(self, obj):
        return obj.status


@admin.register(WebhookEvent)
class WebhookEventAdmin(ModelAdmin):
    list_display = ('event_id', 'processed_at')
    search_fields = ('event_id',)
    readonly_fields = ('event_id', 'processed_at')
