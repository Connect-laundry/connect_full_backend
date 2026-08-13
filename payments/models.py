import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings

class Payment(models.Model):
    """Core payment record for order transactions."""
    class Method(models.TextChoices):
        CARD = 'CARD', _('Card')
        TRANS = 'BANK_TRANSFER', _('Bank Transfer')
        CASH = 'CASH', _('Cash on Delivery')
        WALLET = 'WALLET', _('Wallet Balance')
        MOMO = 'MOBILE_MONEY', _('Mobile Money')

    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        SUCCESS = 'SUCCESS', _('Successful')
        FAILED = 'FAILED', _('Failed')
        EXPIRED = 'EXPIRED', _('Expired')
        # A refund was accepted by Paystack but not yet settled.
        REFUND_PENDING = 'REFUND_PENDING', _('Refund pending')
        REFUNDED = 'REFUNDED', _('Refunded')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payments')
    order = models.OneToOneField('ordering.Order', on_delete=models.CASCADE, related_name='payment')
    
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='GHS')
    
    payment_method = models.CharField(max_length=20, choices=Method.choices, default=Method.CARD)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    
    # Cash collection never passes through Paystack and therefore has no
    # provider transaction reference. Online payments always populate this.
    transaction_reference = models.CharField(max_length=100, unique=True, null=True, blank=True)
    paystack_reference = models.CharField(max_length=100, null=True, blank=True)

    amount_collected = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cash_payments_collected',
    )
    
    raw_response = models.JSONField(null=True, blank=True)

    #: True when Paystack settled this straight to the laundry's subaccount, so
    #: the platform never held the money and owes nothing onward. Recorded at
    #: initialization because the laundry's routing may change afterwards.
    settled_directly = models.BooleanField(default=False)

    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment for {self.order.order_no} ({self.status})"

    # A settled payment can still be refunded, so SUCCESS is only terminal for
    # forward progress. Everything else is a dead end.
    ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.SUCCESS, Status.FAILED, Status.EXPIRED},
        Status.SUCCESS: {Status.REFUND_PENDING, Status.REFUNDED},
        Status.REFUND_PENDING: {Status.REFUNDED, Status.SUCCESS},
        Status.FAILED: set(),
        Status.EXPIRED: set(),
        Status.REFUNDED: set(),
    }

    def transition_to(self, new_status, save=True):
        """Strict transition function for Payment state machine."""
        if self.status == new_status:
            return False

        allowed = self.ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition payment from '{self.status}' to '{new_status}'"
            )

        self.status = new_status
        if save:
            self.save(update_fields=['status', 'updated_at'])
        return True

class WebhookEvent(models.Model):
    """Tracks processed webhook events to prevent double processing."""
    event_id = models.CharField(max_length=255, unique=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.event_id


class Payout(models.Model):
    """
    One transfer of money from the platform to one laundry.

    Customers pay into the platform's Paystack account, so every successful
    payment creates a debt to the laundry that performed the work. A payout is
    the discharge of a batch of those debts. Without this record the platform's
    liability to its vendors exists only in someone's head.
    """

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        PROCESSING = 'PROCESSING', _('Processing')
        PAID = 'PAID', _('Paid')
        FAILED = 'FAILED', _('Failed')

    class Method(models.TextChoices):
        BANK = 'BANK', _('Bank transfer')
        MOMO = 'MOMO', _('Mobile money')
        PAYSTACK = 'PAYSTACK_TRANSFER', _('Paystack transfer')
        MANUAL = 'MANUAL', _('Manual / cash')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        'laundries.Laundry', on_delete=models.PROTECT, related_name='payouts'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='GHS')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.MANUAL)

    reference = models.CharField(max_length=100, blank=True, default='')
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)

    paid_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Payout')
        verbose_name_plural = _('Payouts')
        indexes = [
            models.Index(fields=['laundry', 'status']),
        ]

    def __str__(self):
        return f"Payout {self.amount} {self.currency} to {self.laundry_id} ({self.status})"


class OrderSettlement(models.Model):
    """
    What the platform owes a laundry for a single paid order.

    Amounts are copied from the order's frozen price snapshot rather than
    recomputed, so a later price change cannot alter a debt that has already
    been incurred.
    """

    class Status(models.TextChoices):
        # Paid by the customer, not yet earned. The platform is holding this
        # money and will not pay it out until the order is delivered.
        HELD = 'HELD', _('Held until delivered')
        # Earned and owed to the laundry, not yet attached to a payout.
        PENDING = 'PENDING', _('Ready to pay out')
        # Attached to a payout that has not completed.
        SCHEDULED = 'SCHEDULED', _('Scheduled')
        PAID = 'PAID', _('Paid')
        # The customer was refunded, so the debt no longer stands.
        REVERSED = 'REVERSED', _('Reversed')

    class Route(models.TextChoices):
        #: Collected by the platform, owed onward, paid out later.
        PLATFORM = 'PLATFORM', _('Collected by platform')
        #: Settled to the laundry's subaccount at charge time. Recorded for
        #: reporting; nothing is outstanding because nothing was held.
        DIRECT = 'DIRECT', _('Settled directly')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(
        'ordering.Order', on_delete=models.CASCADE, related_name='settlement'
    )
    laundry = models.ForeignKey(
        'laundries.Laundry', on_delete=models.PROTECT, related_name='settlements'
    )
    payout = models.ForeignKey(
        Payout, on_delete=models.SET_NULL, null=True, blank=True, related_name='settlements'
    )

    #: What the customer actually paid.
    gross_amount = models.DecimalField(max_digits=10, decimal_places=2)
    #: The platform's cut. Zero while the app is free to use.
    platform_commission = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    #: Paystack's cut, when the webhook reports it. Not deducted automatically:
    #: whether the laundry or the platform absorbs it is a commercial decision.
    processor_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    #: gross - commission. What the laundry is owed.
    net_payable = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='GHS')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    route = models.CharField(max_length=20, choices=Route.choices, default=Route.PLATFORM)
    #: When a held settlement becomes payable without further action. Set on
    #: deliveries that were closed without a handover code, giving the customer
    #: a window to dispute before the laundry is paid. Null means the money is
    #: released by an event rather than by the clock.
    release_after = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Order settlement')
        verbose_name_plural = _('Order settlements')
        indexes = [
            models.Index(fields=['laundry', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Settlement {self.net_payable} {self.currency} ({self.status})"
