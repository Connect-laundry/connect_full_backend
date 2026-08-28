import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation

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


def _already_processed(dedup_key):
    """Cheap pre-check so repeat deliveries skip the transaction entirely."""
    return WebhookEvent.objects.filter(event_id=dedup_key).exists()


def _claim_event(dedup_key):
    """Reserve this webhook event, returning False if it is already claimed.

    Must be called inside the transaction that performs the work: the unique
    constraint makes concurrent deliveries safe, and a rollback releases the
    claim so Paystack's retry can reprocess a failed attempt.
    """
    try:
        # A nested atomic block keeps an IntegrityError from poisoning the
        # outer transaction on PostgreSQL.
        with transaction.atomic():
            WebhookEvent.objects.create(event_id=dedup_key)
        return True
    except IntegrityError:
        return False


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

    if str(data.get('reference') or '') != payment.transaction_reference:
        return False, 'reference_mismatch'

    if expected_minor is None or amount_minor != expected_minor:
        return False, 'amount_mismatch'
    if currency != expected_currency:
        return False, 'currency_mismatch'

    metadata_order_id = str(metadata.get('order_id') or '')
    metadata_user_id = str(metadata.get('user_id') or '')
    if metadata_order_id != str(payment.order_id):
        return False, 'order_mismatch'
    if metadata_user_id != str(payment.user_id):
        return False, 'user_mismatch'

    provider_domain = str(data.get('domain') or '').lower()
    secret_key = str(settings.PAYSTACK_SECRET_KEY or '')
    if secret_key.startswith('sk_live_') and provider_domain != 'live':
        return False, 'environment_mismatch'
    if secret_key.startswith('sk_test_') and provider_domain != 'test':
        return False, 'environment_mismatch'

    return True, None


def _refund_reference(data):
    """Original charge reference carried on a Paystack refund event."""
    txn = data.get('transaction') if isinstance(data.get('transaction'), dict) else {}
    return txn.get('reference') or data.get('transaction_reference') or data.get('reference')


def _paystack_fee(data):
    """
    Paystack's cut of a transaction, in cedis.

    Paystack reports `fees` in the smallest currency unit (pesewas). Absent or
    unparseable means zero rather than an error: the settlement is still
    correct without it, since the fee is recorded for visibility and not
    deducted automatically.
    """
    if not isinstance(data, dict):
        return Decimal('0.00')
    raw = data.get('fees')
    if raw is None:
        return Decimal('0.00')
    try:
        return (Decimal(str(raw)) / Decimal('100')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _handle_transfer_event(request, event_type, event_data, dedup_key):
    """
    Apply the outcome of a payout transfer.

    A failed or reversed transfer returns the settlements to the payable pool:
    the laundry is still owed the money, and losing that from the ledger
    because a bank rejected an account number would be worse than the failure
    itself.
    """
    from .models import Payout
    from .services.payout_service import PayoutService

    data = event_data.get('data', {}) if isinstance(event_data.get('data'), dict) else {}
    reference = data.get('reference')
    if not reference:
        logger.warning("Transfer webhook carried no reference")
        return HttpResponse(status=400)

    try:
        with transaction.atomic():
            payout = Payout.objects.select_for_update().filter(reference=reference).first()
            if not payout:
                logger.error(
                    "Payout not found for transfer webhook",
                    extra={"reference": mask_reference(reference)},
                )
                return HttpResponse(status=503)

            if not _claim_event(dedup_key):
                logger.info("Duplicate transfer event ignored", extra={"event_id": dedup_key})
                return HttpResponse(status=200)

            if event_type == 'transfer.success':
                PayoutService.mark_transfer_settled(payout, reference=reference)
            else:
                PayoutService.mark_transfer_failed(
                    payout,
                    reason=data.get('reason') or event_type,
                )

            logger.info(
                "Transfer webhook applied",
                extra={"reference": mask_reference(reference), "event": event_type},
            )
    except Exception as e:
        logger.error(
            "Error processing transfer webhook",
            extra={"reference": mask_reference(reference), "error": summarize_exception(e)},
        )
        return HttpResponse(status=500)

    return HttpResponse(status=200)


def _handle_refund_event(request, event_type, event_data, dedup_key):
    """Apply a refund lifecycle event, claiming it inside the transaction."""
    from .services.refund import mark_refund_failed, mark_refund_settled

    data = event_data.get('data', {}) if isinstance(event_data.get('data'), dict) else {}
    reference = _refund_reference(data)
    if not reference:
        logger.warning("Refund webhook carried no transaction reference")
        return HttpResponse(status=400)

    payment_order_id = Payment.objects.filter(
        transaction_reference=reference
    ).values_list('order_id', flat=True).first()
    if not payment_order_id:
        logger.error(
            "Payment not found for refund webhook",
            extra={"reference": mask_reference(reference)},
        )
        return HttpResponse(status=503)

    try:
        with transaction.atomic():
            Order.objects.select_for_update().get(pk=payment_order_id)
            payment = Payment.objects.select_for_update().filter(
                transaction_reference=reference,
                order_id=payment_order_id,
            ).first()
            if not payment:
                return HttpResponse(status=503)

            if not _claim_event(dedup_key):
                logger.info("Duplicate refund event ignored", extra={"event_id": dedup_key})
                return HttpResponse(status=200)

            if event_type == 'refund.processed':
                mark_refund_settled(payment, request=request)
            elif event_type == 'refund.failed':
                mark_refund_failed(payment, request=request)
            # refund.pending needs no state change: refund_payment already
            # moved the payment to REFUND_PENDING when it was requested.

            logger.info(
                "Refund webhook applied",
                extra={"reference": mask_reference(reference), "event": event_type},
            )
    except Exception as e:
        logger.error(
            "Error processing refund webhook",
            extra={"reference": mask_reference(reference), "error": summarize_exception(e)},
        )
        return HttpResponse(status=500)

    return HttpResponse(status=200)


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

    # Constant-time compare — a plain != leaks the digest byte by byte.
    if not hmac.compare_digest(hash_computed, signature):
        logger.warning("Invalid Paystack webhook signature detected.")
        return HttpResponse(status=401)

    # 2. Parse event data
    try:
        event_data = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    event_id = event_data.get('data', {}).get('id')
    event_type = event_data.get('event')

    # 3. Webhook replay protection key. The row is claimed *inside* the
    # processing transaction below (not here), so that a transient failure
    # rolls the claim back and Paystack's retry can reprocess the event.
    # Claiming it up front would burn the key on a failed attempt and leave a
    # real payment permanently unconfirmed.
    dedup_key = str(event_id) if event_id else 'sha512:' + hash_computed

    if _already_processed(dedup_key):
        logger.info("Duplicate webhook event ignored", extra={"event_id": dedup_key})
        return HttpResponse(status=200)

    # 4a. Refund lifecycle. Paystack settles refunds asynchronously, so these
    # events carry a refund object whose `transaction.reference` points back
    # at the original charge.
    if event_type in ('refund.processed', 'refund.failed', 'refund.pending'):
        return _handle_refund_event(request, event_type, event_data, dedup_key)

    # 4b. Outbound transfers to laundries. Paystack accepts a transfer request
    # and confirms the outcome later, so a payout is not settled until one of
    # these arrives.
    if event_type in ('transfer.success', 'transfer.failed', 'transfer.reversed'):
        return _handle_transfer_event(request, event_type, event_data, dedup_key)

    # 4. Only handle charge.success
    if event_type == 'charge.success':
        data = event_data.get('data', {})
        reference = data.get('reference')

        if not reference:
            return HttpResponse(status=400)

        # Resolve the order before claiming the event. A provider callback can
        # beat local persistence during a failure, and acknowledging an unknown
        # reference would permanently consume a real payment event.
        payment_order_id = Payment.objects.filter(
            transaction_reference=reference
        ).values_list('order_id', flat=True).first()
        if not payment_order_id:
            logger.error(
                "Payment record not found for webhook reference",
                extra={"reference": mask_reference(reference)},
            )
            return HttpResponse(status=503)

        try:
            with transaction.atomic():
                # Keep the global lock order consistent: Order, then Payment.
                # Initialization and lifecycle paths use the same order so a
                # callback racing a button tap cannot deadlock the database.
                order = Order.objects.select_for_update().filter(pk=payment_order_id).first()
                payment = Payment.objects.select_for_update().filter(
                    transaction_reference=reference,
                    order_id=payment_order_id,
                ).first()
                if not order or not payment:
                    logger.error(
                        "Payment disappeared while processing webhook",
                        extra={"reference": mask_reference(reference)},
                    )
                    return HttpResponse(status=503)

                # Claim the event atomically with the work it authorises. A
                # transient failure rolls both the claim and the state change
                # back so Paystack's retry can safely reprocess it.
                if not _claim_event(dedup_key):
                    logger.info(
                        "Duplicate webhook event ignored (concurrent delivery)",
                        extra={"event_id": dedup_key},
                    )
                    return HttpResponse(status=200)

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
                
                # Record what this laundry is now owed. Inside the same
                # transaction as the payment: money confirmed without a
                # matching debt is money nobody knows to pay onward.
                from .services.settlement_service import SettlementService
                SettlementService.record_for_order(
                    order,
                    processor_fee=_paystack_fee(event_data.get('data', {})),
                    settled_directly=payment.settled_directly,
                )

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
                    dedup_key=f"payment_success_user:{payment.id}"
                )

                # Tell the laundry the same thing at the same moment. Customer
                # and owner then hold the identical order number and amount, so
                # "I've paid" can be checked on the spot instead of taken on
                # trust or waited out until settlement.
                owner = getattr(order.laundry, 'owner', None)
                if owner is not None:
                    NotificationService.notify_user(
                        user=owner,
                        title="Payment Received",
                        body=(
                            f"GHS {payment.amount} received for order {order.order_no}. "
                            f"You can start this order."
                        ),
                        type=Notification.Type.ORDER,
                        category="PAYMENT_RECEIVED",
                        related_order=order,
                        dedup_key=f"pay_received_owner_{payment.id}",
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
