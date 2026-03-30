import uuid
from decimal import Decimal
import logging
from django.conf import settings
from payments.services.paystack import PaystackService

logger = logging.getLogger(__name__)


class PaymentService:
    """
    Production payment service integrating with Paystack.
    """

    @staticmethod
    def create_payment_intent(order, payment_method='CARD'):
        """
        Initializes a Paystack transaction and creates a Payment record.
        """
        from payments.models import Payment
        paystack = PaystackService()
        email = order.user.email
        # Use final_price if it's set (e.g. after weighing), else
        # estimated_price
        amount = order.final_price if order.final_price > 0 else order.estimated_price

        # Safe string conversion for UUID
        order_ref = str(order.id).replace('-', '')[:10]
        reference = f"ORD-{order_ref}-{uuid.uuid4().hex[:6]}"

        response = paystack.initialize_transaction(email, amount, reference)

        if response and response.get('status'):
            data = response.get('data', {})

            # Create Payment record for tracking
            Payment.objects.create(
                order=order,
                user=order.user,
                amount=amount,
                payment_method=payment_method,
                transaction_reference=reference,
                status='PENDING'
            )

            return {
                "transaction_id": reference,
                "amount": str(amount),
                "currency": getattr(settings, 'CURRENCY', 'GHS'),
                "status": "PENDING",
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
