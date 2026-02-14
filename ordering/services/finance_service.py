from decimal import Decimal
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.db.models import Sum, F

class FinanceService:
    """
    Centralized service for handling all financial calculations.
    Ensures consistency across views and orders.
    """

    @staticmethod
    def calculate_tax_amount(amount, tax_rate=None):
        """Returns the tax amount for a given base amount."""
        if tax_rate is None:
            tax_rate = Decimal(str(settings.TAX_RATE))
        else:
            tax_rate = Decimal(str(tax_rate))
            
        return (Decimal(str(amount)) * tax_rate).quantize(Decimal('0.01'))

    @staticmethod
    def calculate_delivery_fee(order):
        """Calculates the delivery fee for an order."""
        # Use laundry's specific delivery fee, fallback to global base
        laundry_fee = getattr(order.laundry, 'delivery_fee', None)
        if laundry_fee is not None:
            return Decimal(str(laundry_fee))
            
        return Decimal(str(settings.DELIVERY_FEE_BASE))

    @staticmethod
    def calculate_price_breakdown(order, coupon=None):
        """
        Calculates full financial snapshot for an order.
        Returns dict with: items_total, delivery_fee, discount, tax, platform_fee, total
        """
        # 1. Sum items
        items_total = order.items.aggregate(
            total=Sum(F('quantity') * F('price'))
        )['total'] or Decimal('0.00')
        
        # 2. Delivery Fee
        delivery_fee = FinanceService.calculate_delivery_fee(order)
        
        # 3. Discount
        discount = Decimal('0.00')
        if coupon:
            is_valid, error = coupon.is_valid(
                user=order.user, 
                laundry_id=order.laundry_id, 
                order_value=items_total
            )
            
            if is_valid:
                if coupon.discount_type == 'FIXED':
                    discount = Decimal(str(coupon.discount_value))
                else:
                    discount = (items_total * (Decimal(str(coupon.discount_value)) / 100))
                
                # Ensure discount doesn't exceed items_total
                discount = min(discount, items_total)
        
        # 4. Tax (Calculated on items_total - discount)
        taxable_amount = max(Decimal('0.00'), items_total - discount)
        tax = FinanceService.calculate_tax_amount(taxable_amount)
        
        # 5. Platform Fee
        platform_fee_rate = Decimal(str(settings.PLATFORM_FEE_RATE))
        platform_fee = (taxable_amount * platform_fee_rate).quantize(Decimal('0.01'))
        
        # 6. Final Total
        total = taxable_amount + delivery_fee + tax + platform_fee
        
        return {
            "items_total": str(items_total.quantize(Decimal('0.01'))),
            "delivery_fee": str(delivery_fee.quantize(Decimal('0.01'))),
            "discount": str(discount.quantize(Decimal('0.01'))),
            "tax": str(tax.quantize(Decimal('0.01'))),
            "platform_fee": str(platform_fee.quantize(Decimal('0.01'))),
            "total": str(total.quantize(Decimal('0.01'))),
            "currency": "GHS" # Standardized
        }
