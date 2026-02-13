import uuid
from decimal import Decimal

class PaymentService:
    """Mock payment service to simulate Stripe/Paystack integration."""
    
    @staticmethod
    def create_payment_intent(order):
        """Simulate creating a payment intent and return a mock transaction ID."""
        # In a real scenario, this would call Stripe API
        # pyre-ignore[assignment]
        return {
            "transaction_id": f"TXN-{uuid.uuid4().hex[:12].upper()}",
            "amount": order.total_amount,
            "currency": "GHS",
            "status": "PENDING"
        }

    @staticmethod
    def verify_payment(transaction_id):
        """Simulate verifying a payment status."""
        return True # Mock success
