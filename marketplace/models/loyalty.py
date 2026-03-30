# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
import uuid


class LoyaltyPoint(models.Model):
    """
    Tracks loyalty points for users.
    Points are earned per completed order.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='loyalty_profile')
    points = models.IntegerField(default=0)
    total_earned = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} - {self.points} pts"

    class Meta:
        verbose_name = "Loyalty Point"
        verbose_name_plural = "Loyalty Points"


class LoyaltyTransaction(models.Model):
    """
    Audit trail for point changes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    loyalty_profile = models.ForeignKey(
        LoyaltyPoint,
        on_delete=models.CASCADE,
        related_name='transactions')
    amount = models.IntegerField()
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.loyalty_profile.user.email}: {self.amount}"
