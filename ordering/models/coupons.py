import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
from decimal import Decimal

class Coupon(models.Model):
    class DiscountType(models.TextChoices):
        PERCENTAGE = 'PERCENTAGE', _('Percentage')
        FIXED = 'FIXED', _('Fixed Amount')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    
    discount_type = models.CharField(
        max_length=20, 
        choices=DiscountType.choices, 
        default=DiscountType.PERCENTAGE
    )
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    
    # Cap for percentage discounts
    max_discount_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text=_("Maximum discount allowed (for percentage type).")
    )
    
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    valid_from = models.DateTimeField(default=timezone.now)
    valid_to = models.DateTimeField(null=True, blank=True)
    
    max_usage = models.PositiveIntegerField(null=True, blank=True, help_text=_("Total times this coupon can be used."))
    current_usage = models.PositiveIntegerField(default=0)
    
    user_limit = models.PositiveIntegerField(default=1, help_text=_("Times a single user can use this coupon."))
    
    is_active = models.BooleanField(default=True, db_index=True)
    
    # Multi-laundry support is better
    applicable_laundries = models.ManyToManyField(
        'laundries.Laundry', 
        blank=True, 
        related_name='coupons'
    )
    
    first_time_users_only = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self, user=None, laundry_id=None, order_value=0):
        now = timezone.now()
        
        if not self.is_active:
            return False, _("Coupon is inactive.")
            
        if self.valid_from > now:
            return False, _("Coupon is not yet valid.")
            
        if self.valid_to and self.valid_to < now:
            return False, _("Coupon has expired.")
            
        if self.max_usage is not None and self.current_usage >= self.max_usage:
            return False, _("Coupon has reached its usage limit.")
            
        if Decimal(str(order_value)) < self.min_order_value:
            return False, _(f"Minimum order value of {self.min_order_value} required.")
            
        if self.applicable_laundries.exists():
            if not laundry_id or not self.applicable_laundries.filter(id=laundry_id).exists():
                return False, _("This coupon is not valid for this laundry.")

        if user:
            # Check user usage limit
            usage_count = CouponUsage.objects.filter(user=user, coupon=self).count()
            if usage_count >= self.user_limit:
                return False, _("You have reached your usage limit for this coupon.")
            
            # Check first time users
            if self.first_time_users_only:
                # pyre-ignore[missing-module]
                from .base import Order
                if Order.objects.filter(user=user, status='COMPLETED').exists():
                    return False, _("This coupon is only available for first-time orders.")

        return True, ""

    def calculate_discount(self, order_value):
        order_value = Decimal(str(order_value))
        if self.discount_type == self.DiscountType.FIXED:
            discount = self.discount_value
        else:
            discount = order_value * (self.discount_value / Decimal('100'))
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)
        
        return min(discount, order_value)

    def __str__(self):
        return f"{self.code} ({self.discount_value} {self.discount_type})"

class CouponUsage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='coupon_usages')
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='usages')
    order = models.OneToOneField('ordering.Order', on_delete=models.CASCADE, related_name='coupon_usage')
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'coupon', 'order']
