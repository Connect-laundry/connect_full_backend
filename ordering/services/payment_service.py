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
        paystack = PaystackService()
        email = order.user.email
        amount = order.total_amount
        # Safe string conversion for UUID
        order_ref = str(order.id).replace('-', '')[:10]
        reference = f"ORD-{order_ref}-{uuid.uuid4().hex[:6]}"

        if normalized_method == Payment.Method.CASH:
            cash_reference = f"COD-{order_ref}-{uuid.uuid4().hex[:6]}"
            Payment.objects.update_or_create(
                order=order,
                defaults={
                    'user': order.user,
                    'amount': amount,
                    'currency': settings.PAYMENT_CURRENCY,
                    'payment_method': Payment.Method.CASH,
                    'transaction_reference': cash_reference,
                    'status': Payment.Status.PENDING,
                    'paystack_reference': None,
                }
            )
            return {
                "transaction_id": cash_reference,
                "amount": str(amount),
                "currency": settings.PAYMENT_CURRENCY,
                "status": "PENDING",
                "payment_method": Payment.Method.CASH,
                "authorization_url": None,
                "access_code": None,
            }
        
        metadata = {
            'order_id': str(order.id),
            'user_id': str(order.user_id),
            'order_no': order.order_no,
        }

        response = paystack.initialize_transaction(email, amount, reference, metadata=metadata)
        
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
                    'status': 'PENDING'
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
        
        logger.error(f"Paystack init failed for Order {order.id}: {response}")
        return {
            "transaction_id": reference,
            "status": "FAILED",
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
