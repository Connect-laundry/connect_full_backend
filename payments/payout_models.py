import uuid
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _


class BankAccount(models.Model):
    """Owner's linked bank account for revenue withdrawals."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bank_accounts"
    )
    bank_name = models.CharField(_("bank name"), max_length=100)
    account_name = models.CharField(_("account name"), max_length=150)
    account_number = models.CharField(_("account number"), max_length=20)
    bank_code = models.CharField(
        _("bank code"), max_length=10, help_text="Paystack bank code for transfers"
    )
    is_primary = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Bank Account")
        verbose_name_plural = _("Bank Accounts")
        ordering = ["-is_primary", "-created_at"]

    def __str__(self):
        return f"{self.bank_name} - {self.account_number[-4:]}"

    def save(self, *args, **kwargs):
        if self.is_primary:
            BankAccount.objects.filter(owner=self.owner).update(is_primary=False)
        super().save(*args, **kwargs)


class PayoutRequest(models.Model):
    """Tracks a payout request from a laundry owner."""

    class PayoutStatus(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        PROCESSING = "PROCESSING", _("Processing")
        COMPLETED = "COMPLETED", _("Completed")
        FAILED = "FAILED", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payout_requests",
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="payouts"
    )
    amount = models.DecimalField(_("amount"), max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="GHS")
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=PayoutStatus.choices,
        default=PayoutStatus.PENDING,
    )
    reference = models.CharField(_("reference"), max_length=100, unique=True)
    notes = models.TextField(blank=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Payout Request")
        verbose_name_plural = _("Payout Requests")
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Payout {self.reference} ({self.status})"
