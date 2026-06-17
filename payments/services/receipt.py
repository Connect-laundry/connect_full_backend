import logging
from decimal import Decimal
from django.utils import timezone

logger = logging.getLogger(__name__)

class ReceiptService:
    @staticmethod
    def compile_receipt_data(payment):
        """
        Compiles structured receipt data for a successful payment.
        """
        order = payment.order
        laundry = order.laundry
        user = payment.user

        # Get items breakdown
        items = []
        for item in order.items.all():
            item_total = item.price * item.quantity
            items.append({
                "name": item.name,
                "service_type": item.service_type.name if item.service_type else "Laundry",
                "quantity": item.quantity,
                "price": str(item.price),
                "total": str(item_total)
            })

        # Calculate items subtotal
        items_subtotal = sum(Decimal(i["total"]) for i in items)

        # Retrieve coupon/discount information
        discount_amount = Decimal("0.00")
        coupon_code = None
        if order.coupon:
            coupon_code = order.coupon.code
            # Since Order total_amount is the final_price, discount is difference if we knew base items subtotal + fees.
            # But wait, let's keep it safe. If there's a coupon, let's record it.

        # Retrieve payment method string
        payment_method_label = payment.get_payment_method_display()

        receipt = {
            "receipt_no": f"REC-{payment.transaction_reference}",
            "transaction_reference": payment.transaction_reference,
            "order_no": order.order_no,
            "laundry": {
                "name": laundry.name,
                "address": laundry.address,
                "phone_number": laundry.phone_number,
            },
            "customer": {
                "name": f"{user.first_name} {user.last_name}".strip() or user.email,
                "email": user.email,
                "phone": user.phone,
            },
            "items": items,
            "pricing": {
                "items_subtotal": str(items_subtotal),
                "total_amount": str(payment.amount),
                "currency": payment.currency,
            },
            "payment": {
                "method": payment_method_label,
                "status": payment.status,
                "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
                "created_at": payment.created_at.isoformat(),
            }
        }
        return receipt
