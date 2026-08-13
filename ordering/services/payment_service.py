import uuid
import logging
from payments.services.paystack import PaystackService
from django.conf import settings

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
        """
        Initializes a Paystack transaction and creates a Payment record.
        """
        from payments.models import Payment
        normalized_method = PaymentService._normalize_payment_method(payment_method)
        email = order.user.email
        amount = order.total_amount
        # Safe string conversion for UUID
        order_ref = str(order.id).replace('-', '')[:10]
        reference = f"ORD-{order_ref}-{uuid.uuid4().hex[:6]}"

        if normalized_method == Payment.Method.CASH:
            # COD is a promise to pay at fulfillment, not a gateway payment.
            # The successful cash Payment row is created only when the owner
            # explicitly confirms receipt.
            return {
                "transaction_id": None,
                "amount": str(amount),
                "currency": settings.PAYMENT_CURRENCY,
                "status": "CASH_DUE",
                "payment_method": Payment.Method.CASH,
                "authorization_url": None,
                "access_code": None,
            }
        paystack = PaystackService()
        metadata = {
            'order_id': str(order.id),
            'user_id': str(order.user_id),
            'order_no': order.order_no,
        }

        # Route the money. Direct settlement sends it to the laundry's own
        # Paystack subaccount; otherwise it lands with the platform and the
        # settlement ledger records what is owed.
        from payments.services.split_routing import resolve_route
        route = resolve_route(order)
        if route.is_direct:
            metadata['settlement'] = 'DIRECT'
            metadata['subaccount'] = route.subaccount_code

        response = paystack.initialize_transaction(
            email,
            amount,
            reference,
            metadata=metadata,
            subaccount=route.subaccount_code or None,
            transaction_charge=route.platform_charge_pesewas,
            bearer=route.bearer if route.is_direct else None,
        )

        if response and response.get('status'):
            data = response.get('data', {})
            
            # Create Payment record for tracking
            Payment.objects.update_or_create(
                order=order,
                defaults={
                    'user': order.user,
                    'amount': amount,
                    'currency': settings.PAYMENT_CURRENCY,
                    'payment_method': normalized_method,
                    'transaction_reference': reference,
                    'status': 'PENDING',
                    'paystack_reference': data.get('access_code'),
                    # Recorded now, not at webhook time: the laundry's routing
                    # could change between charge and confirmation, and this
                    # transaction's fate was decided here.
                    'settled_directly': route.is_direct,
                }
            )
            
            return {
                "transaction_id": reference,
                "amount": str(amount),
                "currency": settings.PAYMENT_CURRENCY,
                "status": "PENDING",
                "payment_method": normalized_method,
                "authorization_url": data.get('authorization_url'),
                "access_code": data.get('access_code')
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
            "message": "Payment initialization failed."
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
