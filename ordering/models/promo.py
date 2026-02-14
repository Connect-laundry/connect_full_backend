import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
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
    
    max_discount_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Only relevant for PERCENTAGE type"
    )
    min_order_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0.00
    )
    
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    
    applicable_laundries = models.ManyToManyField(
        'laundries.Laundry', 
        blank=True, 
        related_name='available_coupons'
    )
    
    first_time_users_only = models.BooleanField(default=False)
    
    expires_at = models.DateTimeField(db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Coupon')
        verbose_name_plural = _('Coupons')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return self.code.upper()

    def save(self, *args, **kwargs):
        self.code = self.code.upper()
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_limit_reached(self):
        if self.usage_limit is None:
            return False
        return self.used_count >= self.usage_limit

    def validate_for_user(self, user):
        """Checks if user is eligible for the coupon."""
        if self.first_time_users_only:
            # pyre-ignore[missing-module]
            from .base import Order
            has_orders = Order.objects.filter(user=user, status='COMPLETED').exists()
            if has_orders:
                return False, "This coupon is only for first-time users."
        return True, ""

    def validate_for_order(self, order_amount, user, laundry_id=None):
        """
        Validates coupon against order constraints.
        Returns (is_valid, message, discount_amount)
        """
        if not self.is_active:
            return False, "Coupon is inactive", 0
            
        if self.is_expired:
            return False, "Coupon has expired", 0
            
        if self.is_limit_reached:
            return False, "Coupon usage limit reached", 0
            
        if order_amount < self.min_order_amount:
            return False, f"Order amount must be at least {self.min_order_amount}", 0

        if self.applicable_laundries.exists():
            if not laundry_id or not self.applicable_laundries.filter(id=laundry_id).exists():
                return False, "Coupon is not applicable to this laundry", 0

        # User eligibility
        is_eligible, user_msg = self.validate_for_user(user)
        if not is_eligible:
            return False, user_msg, 0

        # Calculate discount
        if self.discount_type == self.DiscountType.FIXED:
            discount = min(self.discount_value, order_amount)
        else:
            discount = (self.discount_value / 100) * Decimal(str(order_amount))
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)

        # Never allow discount > order_amount
        discount = min(discount, Decimal(str(order_amount)))

        return True, "Coupon valid", discount
