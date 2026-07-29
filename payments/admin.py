from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
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
        'refund_link',
    )
    list_filter = ('status', 'payment_method', 'created_at')
    search_fields = ('transaction_reference', 'paystack_reference', 'order__order_no', 'user__email')
    readonly_fields = ('transaction_reference', 'paystack_reference', 'raw_response', 'created_at', 'updated_at')
    # Refunds are deliberately NOT a bulk action: they move money irreversibly,
    # so each one goes through a per-payment confirmation screen.
    actions = ['force_reconcile']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('order', 'user')

    # ------------------------------------------------------------- refunds

    def get_urls(self):
        return [
            path(
                '<path:object_id>/refund/',
                self.admin_site.admin_view(self.refund_view),
                name='payments_payment_refund',
            ),
        ] + super().get_urls()

    @display(description="Refund")
    def refund_link(self, obj):
        if obj.status == Payment.Status.REFUNDED:
            return format_html('<span class="text-gray-400">Refunded</span>')
        if obj.status == Payment.Status.REFUND_PENDING:
            return format_html('<span class="text-amber-600">In progress</span>')
        if obj.status != Payment.Status.SUCCESS:
            return "—"
        url = reverse('admin:payments_payment_refund', args=[obj.pk])
        return format_html('<a href="{}" class="text-red-600 underline">Refund</a>', url)

    def refund_view(self, request, object_id):
        """Confirm (GET) then start (POST) a refund for one payment."""
        from .services.refund import RefundError, refund_payment

        payment = self.get_object(request, object_id)
        if payment is None:
            messages.error(request, "Payment not found.")
            return redirect(reverse('admin:payments_payment_changelist'))

        changelist = reverse('admin:payments_payment_changelist')

        if payment.status != Payment.Status.SUCCESS:
            messages.error(
                request,
                f"Only a successful payment can be refunded (this one is '{payment.status}').",
            )
            return redirect(changelist)

        if request.method != 'POST':
            context = {
                **self.admin_site.each_context(request),
                'title': f"Refund — {payment.transaction_reference}",
                'payment': payment,
                'opts': self.model._meta,
            }
            return render(request, 'admin/payments/refund_confirm.html', context)

        raw_amount = (request.POST.get('amount') or '').strip()
        amount = None
        if raw_amount:
            try:
                amount = Decimal(raw_amount)
            except (InvalidOperation, ValueError):
                messages.error(request, "Enter a valid refund amount.")
                return redirect(request.path)

        try:
            refund_payment(
                payment,
                amount=amount,
                reason=(request.POST.get('reason') or '').strip(),
                actor=request.user,
                request=request,
            )
        except RefundError as exc:
            messages.error(request, str(exc))
            return redirect(changelist)

        messages.success(
            request,
            f"Refund requested for {payment.transaction_reference}. "
            "It will show as Refunded once Paystack settles it.",
        )
        return redirect(changelist)

    @display(description="Order")
    def order_link(self, obj):
        if not obj or not obj.order:
            return "—"
        try:
            from django.urls import reverse
            url = reverse("admin:ordering_order_change", args=[obj.order.id])
            return format_html('<a href="{}" class="font-mono text-primary-600 underline">{}</a>', url, obj.order.order_no)
        except Exception:
            return getattr(obj.order, 'order_no', '—')

    @display(description="Amount", ordering="amount")
    def display_amount(self, obj):
        return f"{obj.amount} {obj.currency}"

    @display(description="Status", label={
        "SUCCESS": "success",
        "PENDING": "warning",
        "FAILED": "danger",
        "EXPIRED": "warning",
        "REFUND_PENDING": "warning",
        "REFUNDED": "info",
    })
    def display_status(self, obj):
        return obj.status

    @admin.action(description="Force Reconcile via Paystack")
    def force_reconcile(self, request, queryset):
        """Manually force reconciliation of selected payments against Paystack."""
        from payments.services.paystack import PaystackService
        from ordering.services.order_state_machine import OrderStateMachine
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        from django.utils import timezone
        from payments.views import _validate_verified_payment, _sanitize_payment_response
        from django.db import transaction

        paystack = PaystackService()
        reconciled_count = 0
        failed_count = 0

        for payment in queryset:
            if payment.status != Payment.Status.PENDING:
                continue

            try:
                with transaction.atomic():
                    # lock row
                    locked_payment = Payment.objects.select_for_update().filter(id=payment.id).first()
                    if not locked_payment or locked_payment.status != Payment.Status.PENDING:
                        continue

                    verify_data = paystack.verify_transaction(locked_payment.transaction_reference)
                    if verify_data.get('status'):
                        gateway_data = verify_data.get('data', {})
                        status_val = gateway_data.get('status')

                        if status_val == 'success':
                            is_valid, validation_error = _validate_verified_payment(locked_payment, gateway_data)
                            if is_valid:
                                locked_payment.transition_to(Payment.Status.SUCCESS, save=False)
                                locked_payment.raw_response = _sanitize_payment_response(gateway_data, locked_payment.transaction_reference)
                                locked_payment.paid_at = timezone.now()
                                locked_payment.save()

                                order = locked_payment.order
                                order.payment_status = order.PaymentStatus.PAID
                                order.save(update_fields=['payment_status', 'updated_at'])

                                OrderStateMachine.transition(order.id, order.Status.CONFIRMED, user=request.user)

                                NotificationService.notify_user(
                                    user=locked_payment.user,
                                    title="Payment Reconciled",
                                    body=f"Your payment of GHS {locked_payment.amount} for order {order.order_no} has been verified.",
                                    type=Notification.Type.ORDER,
                                    category="PAYMENT_SUCCESS",
                                    related_order=order,
                                    dedup_key=f"reconcile_success_{locked_payment.id}"
                                )
                                reconciled_count += 1
                            else:
                                locked_payment.transition_to(Payment.Status.FAILED, save=False)
                                locked_payment.save(update_fields=['status', 'updated_at'])
                                failed_count += 1
                        elif status_val in ['failed', 'abandoned']:
                            locked_payment.transition_to(Payment.Status.FAILED, save=False)
                            locked_payment.save(update_fields=['status', 'updated_at'])
                            failed_count += 1
            except Exception as e:
                self.message_user(request, f"Error reconciling {payment.transaction_reference}: {str(e)}", level='error')

        self.message_user(
            request, 
            f"Successfully reconciled {reconciled_count} payments. Marked {failed_count} as FAILED."
        )


@admin.register(WebhookEvent)
class WebhookEventAdmin(ModelAdmin):
    list_display = ('event_id', 'processed_at')
    search_fields = ('event_id',)
    readonly_fields = ('event_id', 'processed_at')
