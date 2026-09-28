from django.contrib import admin, messages
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import AdminNotificationDelivery, AdminNotificationEvent, AdminNotificationProviderMessage
from .phone import mask_recipient

STATUS_LABELS = {
    'PENDING': 'warning', 'SENDING': 'info', 'RETRY': 'warning', 'SUBMITTED': 'info', 'SENT': 'success',
    'DELIVERED': 'success', 'UNDELIVERED': 'danger', 'UNKNOWN': 'warning', 'FAILED': 'danger', 'DEAD': 'danger',
}


class ReadOnlyAdminMixin:
    """Outbox rows are written by the system only; admins observe and retry."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DeliveryInline(ReadOnlyAdminMixin, TabularInline):
    model = AdminNotificationDelivery
    extra = 0
    can_delete = False
    fields = ('channel', 'masked_recipient', 'status', 'attempt_count', 'parts_sent', 'parts_total',
              'provider_message_id', 'submitted_at', 'delivered_at', 'last_error_class')
    readonly_fields = fields

    @display(description='Recipient')
    def masked_recipient(self, obj):
        return mask_recipient(obj.recipient)


@admin.register(AdminNotificationEvent)
class AdminNotificationEventAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('display_order', 'event_type', 'created_at', 'processed_at', 'delivery_summary')
    list_filter = ('event_type', 'environment', 'created_at')
    search_fields = ('order__order_no',)
    inlines = [DeliveryInline]
    fields = ('order', 'event_type', 'environment', 'idempotency_key', 'created_at', 'processed_at',
              'message_preview')
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('order').prefetch_related('deliveries')

    @display(description='Order', ordering='order__order_no')
    def display_order(self, obj):
        return obj.order.order_no

    @display(description='Deliveries')
    def delivery_summary(self, obj):
        counts = {}
        for delivery in obj.deliveries.all():
            counts[delivery.status] = counts.get(delivery.status, 0) + 1
        return ', '.join(f'{status}: {count}' for status, count in sorted(counts.items())) or 'none'

    @display(description='Rendered messages')
    def message_preview(self, obj):
        from .rendering import render_sms_parts, render_whatsapp_parts
        try:
            sms = render_sms_parts(obj.payload)
            whatsapp = [part.body for part in render_whatsapp_parts(obj.payload)]
        except Exception as exc:  # pragma: no cover - display only
            return f'Preview unavailable: {type(exc).__name__}'
        blocks = [('WhatsApp', text) for text in whatsapp] + [('SMS', text) for text in sms]
        return format_html_join(
            '', '<p><strong>{}</strong></p><pre style="white-space:pre-wrap;max-width:60ch">{}</pre>', blocks,
        )


@admin.register(AdminNotificationDelivery)
class AdminNotificationDeliveryAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ('display_order', 'display_event_type', 'channel', 'masked_recipient', 'display_status',
                    'attempt_count', 'provider_message_id', 'created_at', 'submitted_at', 'delivered_at',
                    'last_error_class')
    list_filter = ('status', 'channel', 'event__event_type', 'last_error_class', 'created_at')
    search_fields = ('event__order__order_no', 'provider_message_id')
    actions = ['retry_selected']
    fields = ('event', 'channel', 'masked_recipient', 'provider', 'status', 'attempt_count', 'next_attempt_at',
              'parts_sent', 'parts_total', 'estimated_segments', 'provider_message_id', 'last_error_class',
              'last_error_safe_message', 'created_at', 'claimed_at', 'submitted_at', 'delivered_at', 'failed_at',
              'provider_parts')
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('event', 'event__order')

    @display(description='Order', ordering='event__order__order_no')
    def display_order(self, obj):
        return obj.event.order.order_no

    @display(description='Event', ordering='event__event_type')
    def display_event_type(self, obj):
        return obj.event.event_type

    @display(description='Recipient')
    def masked_recipient(self, obj):
        return mask_recipient(obj.recipient)

    @display(description='Status', label=STATUS_LABELS)
    def display_status(self, obj):
        return obj.status

    @display(description='Provider messages')
    def provider_parts(self, obj):
        rows = AdminNotificationProviderMessage.objects.filter(delivery=obj)
        return format_html_join(
            mark_safe('<br>'), 'Part {}: {} {} ({} seg.)',
            ((m.part_index + 1, m.provider_message_id or 'no id', m.provider_status, m.estimated_segments)
             for m in rows),
        ) or '-'

    def has_retry_permission(self, request):
        return request.user.is_active and request.user.is_staff and request.user.has_perm(
            'admin_notifications.change_adminnotificationdelivery')

    @admin.action(description='Retry selected failed / dead / unknown / undelivered notifications',
                  permissions=['retry'])
    def retry_selected(self, request, queryset):
        from .services.dispatcher import requeue
        from .services.kick import kick_dispatch
        unknown = queryset.filter(status='UNKNOWN').count()
        count = requeue(queryset)
        kick_dispatch()
        self.message_user(
            request,
            f'Re-queued {count} notification(s).' + (
                ' UNKNOWN ones may already have been received: expect a possible duplicate.' if unknown else ''),
            level=messages.SUCCESS if count else messages.WARNING,
        )
