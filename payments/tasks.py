import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from django.conf import settings

from config.celery_utils import hardened_task
from payments.models import Payment
from payments.services.paystack import PaystackService
from ordering.services.order_state_machine import OrderStateMachine
from marketplace.services.notification_service import NotificationService
from marketplace.models import Notification

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
@hardened_task(max_retries=3, base_delay=10)
def reconcile_pending_payments(self):
    """
    Hardened background job to reconcile stale pending payments against Paystack.
    Updates states and confirms orders if successfully paid on the gateway.
    """
    now = timezone.now()
    # Check payments created between 10 minutes and 24 hours ago
    cutoff_start = now - timedelta(minutes=10)
    cutoff_end = now - timedelta(hours=24)
    
    pending_payments = Payment.objects.filter(
        status=Payment.Status.PENDING,
        created_at__lte=cutoff_start,
        created_at__gte=cutoff_end
    )
    
    reconciled_count = 0
    paystack = PaystackService()
    
    for payment in pending_payments:
        try:
            with transaction.atomic():
                # Lock row to prevent race conditions with user interactions or webhooks
                locked_payment = Payment.objects.select_for_update().filter(id=payment.id).first()
                if not locked_payment or locked_payment.status != Payment.Status.PENDING:
                    continue
                
                ref = locked_payment.transaction_reference
                verify_data = paystack.verify_transaction(ref)
                
                if verify_data.get('status'):
                    gateway_data = verify_data.get('data', {})
                    status_val = gateway_data.get('status')
                    
                    if status_val == 'success':
                        from payments.views import _validate_verified_payment, _sanitize_payment_response
                        
                        is_valid, validation_error = _validate_verified_payment(locked_payment, gateway_data)
                        if is_valid:
                            # Strict transition using state machine
                            locked_payment.transition_to(Payment.Status.SUCCESS, save=False)
                            locked_payment.raw_response = _sanitize_payment_response(gateway_data, ref)
                            locked_payment.paid_at = timezone.now()
                            locked_payment.save()
                            
                            order = locked_payment.order
                            order.payment_status = order.PaymentStatus.PAID
                            order.save(update_fields=['payment_status', 'updated_at'])
                            
                            # Transition status using OrderStateMachine to trigger signals/notifications
                            OrderStateMachine.transition(order.id, order.Status.CONFIRMED, user=None)
                            
                            # Trigger push notification
                            NotificationService.notify_user(
                                user=locked_payment.user,
                                title="Payment Reconciled Successfully",
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
                            
                            logger.error(
                                "Fraud/Validation failed during reconciliation of payment",
                                extra={"payment_id": str(locked_payment.id), "reason": validation_error}
                            )
                    elif status_val in ['failed', 'abandoned']:
                        locked_payment.transition_to(Payment.Status.FAILED, save=False)
                        locked_payment.save(update_fields=['status', 'updated_at'])
                        
                        # Notify user of failure
                        NotificationService.notify_user(
                            user=locked_payment.user,
                            title="Payment Attempt Failed",
                            body=f"Your payment attempt for order {locked_payment.order.order_no} was declined or abandoned.",
                            type=Notification.Type.ORDER,
                            category="PAYMENT_FAILED",
                            related_order=locked_payment.order,
                            dedup_key=f"reconcile_fail_{locked_payment.id}"
                        )
                else:
                    # If Paystack cannot find reference and transaction is older than 2 hours, mark EXPIRED
                    if locked_payment.created_at < now - timedelta(hours=2):
                        locked_payment.transition_to(Payment.Status.EXPIRED, save=False)
                        locked_payment.save(update_fields=['status', 'updated_at'])
                        
        except Exception as e:
            logger.error(
                f"Failed to reconcile payment {payment.id}",
                extra={"error": str(e), "payment_reference": payment.transaction_reference}
            )
            
    return f"Reconciled {reconciled_count} payments."
