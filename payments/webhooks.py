import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.db import transaction, IntegrityError
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ordering.models import Order
from .models import Payment, WebhookEvent
from config.redaction import mask_reference, summarize_exception
from marketplace.services.audit import record_audit
from ordering.services.order_state_machine import OrderStateMachine
from marketplace.services.notification_service import NotificationService
from marketplace.models import Notification

logger = logging.getLogger(__name__)


def _sanitize_webhook_payload(payload, reference):
    data = payload if isinstance(payload, dict) else {}
    return {
        "reference": reference,
        "status": data.get('status'),
        "gateway_status": data.get('gateway_response') or data.get('gateway_status'),
        "amount": data.get('amount'),
        "currency": data.get('currency', 'GHS'),
    }


def _to_minor_units(amount):
    try:
        from decimal import Decimal

        return int((Decimal(str(amount)) * 100).quantize(Decimal('1')))
    except Exception:
        return None


def _validate_webhook_payment(payment, payload):
    data = payload if isinstance(payload, dict) else {}
    amount_minor = data.get('amount')
    currency = str(data.get('currency') or '').upper()
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}

    expected_minor = _to_minor_units(payment.amount)
    expected_currency = str(payment.currency or settings.PAYMENT_CURRENCY).upper()

    if expected_minor is None or amount_minor != expected_minor:
        return False, 'amount_mismatch'
    if currency != expected_currency:
        return False, 'currency_mismatch'

    metadata_order_id = str(metadata.get('order_id') or '')
    metadata_user_id = str(metadata.get('user_id') or '')
    if metadata_order_id and metadata_order_id != str(payment.order_id):
        return False, 'order_mismatch'
    if metadata_user_id and metadata_user_id != str(payment.user_id):
        return False, 'user_mismatch'

    return True, None


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """
    POST /api/v1/payments/webhook/
    Secure webhook handler for Paystack events.
    """
    secret = settings.PAYSTACK_SECRET_KEY
    payload = request.body
    signature = request.headers.get('x-paystack-signature')

    if not secret:
        # Without the secret we cannot authenticate the sender; 503 lets
        # Paystack retry once configuration is restored.
        logger.error("Paystack webhook received but PAYSTACK_SECRET_KEY is not configured.")
        return HttpResponse(status=503)

    if not signature:
        logger.warning("Webhook received without signature.")
        return HttpResponse(status=401)

    # 1. Verify x-paystack-signature using HMAC SHA512
    hash_computed = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha512
    ).hexdigest()

    if hash_computed != signature:
        logger.warning("Invalid Paystack webhook signature detected.")
        return HttpResponse(status=401)

    # 2. Parse event data
    try:
        event_data = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    event_id = event_data.get('data', {}).get('id')
    event_type = event_data.get('event')

    # 3. Webhook Replay Protection.
    dedup_key = str(event_id) if event_id else 'sha512:' + hash_computed
    try:
        _, created = WebhookEvent.objects.get_or_create(event_id=dedup_key)
    except IntegrityError:
        created = False
    if not created:
        logger.info("Duplicate webhook event ignored", extra={"event_id": dedup_key})
        return HttpResponse(status=200)

    # 4. Only handle charge.success
    if event_type == 'charge.success':
        data = event_data.get('data', {})
        reference = data.get('reference')
        
        if not reference:
            return HttpResponse(status=400)

        # 5. Use transaction.atomic() and select_for_update()
        try:
            with transaction.atomic():
                payment = Payment.objects.select_for_update().filter(transaction_reference=reference).first()
                
                if not payment:
                    logger.error(
                        "Payment record not found for webhook reference",
                        extra={"reference": mask_reference(reference)},
                    )
                    return HttpResponse(status=200) # Safe exit

                # 6. Idempotency Check / Terminal state validation
                if payment.status in [Payment.Status.SUCCESS, Payment.Status.FAILED, Payment.Status.EXPIRED]:
                    logger.info(
                        f"Payment already in terminal state '{payment.status}'; skipping webhook",
                        extra={"reference": mask_reference(reference)},
                    )
                    return HttpResponse(status=200)

                is_valid, failure_reason = _validate_webhook_payment(payment, event_data.get('data', {}))
                if not is_valid:
                    payment.transition_to(Payment.Status.FAILED, save=False)
                    payment.raw_response = _sanitize_webhook_payload(event_data.get('data', {}), reference)
                    payment.save(update_fields=['status', 'raw_response', 'updated_at'])
                    
                    record_audit(
                        action="PAYMENT_WEBHOOK_REJECTED",
                        actor=None,
                        request=request,
                        target_type="Payment",
                        target_id=str(payment.id),
                        target_repr=f"Payment {reference} Webhook Rejected",
                        metadata={"reason": failure_reason, "amount": str(payment.amount)}
                    )
                    
                    NotificationService.notify_user(
                        user=payment.user,
                        title="Payment Attempt Failed",
                        body=f"Your payment attempt for order {payment.order.order_no} failed.",
                        type=Notification.Type.ORDER,
                        category="PAYMENT_FAILED",
                        related_order=payment.order,
                        dedup_key=f"pay_failed_webhook_{payment.id}"
                    )
                    
                    logger.warning(
                        "Webhook rejected",
                        extra={"reference": mask_reference(reference), "reason": failure_reason},
                    )
                    return HttpResponse(status=400)

                # 7. Update Status using strict transitions
                payment.transition_to(Payment.Status.SUCCESS, save=False)
                payment.raw_response = _sanitize_webhook_payload(event_data.get('data', {}), reference)
                payment.paid_at = timezone.now()
                
                # Update payment method dynamically from Paystack gateway channel
                channel = event_data.get('data', {}).get('channel')
                if channel == 'mobile_money':
                    payment.payment_method = Payment.Method.MOMO
                elif channel == 'card':
                    payment.payment_method = Payment.Method.CARD
                elif channel in {'bank', 'bank_transfer', 'transfer'}:
                    payment.payment_method = Payment.Method.TRANS
                
                payment.save()
                
                # Update order payment status
                order = payment.order
                order.payment_status = Order.PaymentStatus.PAID
                order.save(update_fields=['payment_status', 'updated_at'])
                
                # Transition order using OrderStateMachine to trigger audit/history logs & signals
                OrderStateMachine.transition(order.id, Order.Status.CONFIRMED, user=None)
                
                record_audit(
                    action="PAYMENT_WEBHOOK_CONFIRMED",
                    actor=None,
                    request=request,
                    target_type="Payment",
                    target_id=str(payment.id),
                    target_repr=f"Payment {reference} Confirmed via Webhook",
                    metadata={"amount": str(payment.amount), "order_id": str(order.id)}
                )
                
                NotificationService.notify_user(
                    user=payment.user,
                    title="Payment Successful",
                    body=f"Your payment of GHS {payment.amount} for order {order.order_no} was successful.",
                    type=Notification.Type.ORDER,
                    category="PAYMENT_SUCCESS",
                    related_order=order,
                    dedup_key=f"pay_success_webhook_{payment.id}"
                )
                
                logger.info("Webhook success confirmed", extra={"reference": mask_reference(reference)})
        except Exception as e:
            logger.error(
                "Error processing webhook",
                extra={"reference": mask_reference(reference), "error": summarize_exception(e)},
            )
            # We return 500 to let Paystack retry if it's a transient failure
            return HttpResponse(status=500)

    return HttpResponse(status=200)
