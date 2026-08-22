import uuid
import logging
from payments.services.paystack import PaystackService
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

class PaymentService:
    """
    Production payment service integrating with Paystack.
    """

    @staticmethod
    def _normalize_payment_method(payment_method: str | None) -> str:
        normalized = str(payment_method or 'CARD').strip().upper()
        if normalized in {'PAYSTACK', 'CARD'}:
            return 'CARD'
        if normalized in {'CASH', 'CASH_ON_DELIVERY'}:
            return 'CASH'
        if normalized in {'BANK_TRANSFER', 'TRANSFER'}:
            return 'BANK_TRANSFER'
        return 'CARD'
    
    @staticmethod
    def create_payment_intent(order, payment_method='CARD'):
        """Reserve a durable reference, then initialize Paystack without DB locks."""
        from payments.models import Payment

        normalized_method = PaymentService._normalize_payment_method(payment_method)
        amount = order.total_amount
        if normalized_method == Payment.Method.CASH:
            return {
                "transaction_id": None,
                "amount": str(amount),
                "currency": settings.PAYMENT_CURRENCY,
                "status": "CASH_DUE",
                "payment_method": Payment.Method.CASH,
                "authorization_url": None,
                "access_code": None,
            }

        from payments.services.split_routing import resolve_route
        route = resolve_route(order)

        with transaction.atomic():
            locked_order = type(order).objects.select_for_update().get(pk=order.pk)
            existing = Payment.objects.select_for_update().filter(order=locked_order).first()
            if existing and existing.status == Payment.Status.SUCCESS:
                return {
                    "transaction_id": existing.transaction_reference,
                    "amount": str(existing.amount),
                    "currency": existing.currency,
                    "status": "SUCCESS",
                    "payment_method": existing.payment_method,
                    "authorization_url": None,
                    "access_code": existing.paystack_reference,
                }
            if existing and existing.status == Payment.Status.PENDING and existing.paystack_reference:
                return {
                    "transaction_id": existing.transaction_reference,
                    "amount": str(existing.amount),
                    "currency": existing.currency,
                    "status": "PENDING",
                    "payment_method": existing.payment_method,
                    "authorization_url": f"https://checkout.paystack.com/{existing.paystack_reference}",
                    "access_code": existing.paystack_reference,
                }

            stable_pending = (
                existing
                and existing.status == Payment.Status.PENDING
                and existing.payment_method == normalized_method
                and existing.transaction_reference
            )
            if stable_pending:
                reference = existing.transaction_reference
            else:
                order_ref = str(order.id).replace('-', '')[:10]
                reference = f"ORD-{order_ref}-{uuid.uuid4().hex[:6]}"

            payment, _ = Payment.objects.update_or_create(
                order=locked_order,
                defaults={
                    'user': locked_order.user,
                    'amount': amount,
                    'currency': settings.PAYMENT_CURRENCY,
                    'payment_method': normalized_method,
                    'transaction_reference': reference,
                    'status': Payment.Status.PENDING,
                    'paystack_reference': None,
                    'settled_directly': route.is_direct,
                },
            )

        metadata = {
            'order_id': str(order.id),
            'user_id': str(order.user_id),
            'order_no': order.order_no,
        }
        if route.is_direct:
            metadata['settlement'] = 'DIRECT'
            metadata['subaccount'] = route.subaccount_code

        response = PaystackService().initialize_transaction(
            order.user.email,
            amount,
            reference,
            metadata=metadata,
            subaccount=route.subaccount_code or None,
            transaction_charge=route.platform_charge_pesewas,
            bearer=route.bearer if route.is_direct else None,
        )
        data = response.get('data', {}) if isinstance(response.get('data'), dict) else {}
        access_code = data.get('access_code')
        authorization_url = data.get('authorization_url')
        if response.get('status') and access_code and authorization_url:
            Payment.objects.filter(
                pk=payment.pk,
                transaction_reference=reference,
            ).update(paystack_reference=access_code, updated_at=timezone.now())
            return {
                "transaction_id": reference,
                "amount": str(amount),
                "currency": settings.PAYMENT_CURRENCY,
                "status": "PENDING",
                "payment_method": normalized_method,
                "authorization_url": authorization_url,
                "access_code": access_code,
            }

        logger.error("Paystack initialization failed", extra={"order_id": str(order.id)})
        return {
            "transaction_id": reference,
            "amount": str(amount),
            "currency": settings.PAYMENT_CURRENCY,
            "status": "FAILED",
            "payment_method": normalized_method,
            "authorization_url": None,
            "access_code": None,
            "retryable": bool(response.get('retryable') or response.get('indeterminate')),
            "message": response.get('message') or "Payment initialization failed.",
        }
    @staticmethod
    def verify_payment(reference):
        """
        Verifies a Paystack transaction status.
        """
        paystack = PaystackService()
        response = paystack.verify_transaction(reference)
        
        if response and response.get('status'):
            data = response.get('data', {})
            if data.get('status') == 'success':
                return True
        
        return False
