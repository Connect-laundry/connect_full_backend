import uuid
import os
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
# pyre-ignore[missing-module]
from django.core.validators import MinValueValidator, MaxValueValidator
# pyre-ignore[missing-module]
from ..utils.validators import validate_file_upload, validate_latitude, validate_longitude

# Conditionally import GIS or regular Django models based on USE_POSTGIS
USE_POSTGIS = getattr(settings, 'USE_POSTGIS', False)

if USE_POSTGIS:
    # pyre-ignore[missing-module]
    from django.contrib.gis.db import models
    # pyre-ignore[missing-module]
    from django.contrib.gis.db.models import Index
    # pyre-ignore[missing-module]
    from django.contrib.postgres.indexes import GistIndex
else:
    # pyre-ignore[missing-module]
    from django.db import models
    # pyre-ignore[missing-module]
    from django.db.models import Index
    # For non-GIS mode, GistIndex won't be used, but we need a placeholder
    GistIndex = None

class Laundry(models.Model):
    class PriceRange(models.TextChoices):
        LOW = '$', _('Low')
        MEDIUM = '$$', _('Medium')
        HIGH = '$$$', _('High')

    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CHANGES_REQUESTED = "CHANGES_REQUESTED", _("Changes Requested")
        SUSPENDED = "SUSPENDED", _("Suspended")

    class PricingModel(models.TextChoices):
        BY_ITEM = 'BY_ITEM', _('Per item / garment count')
        BY_WEIGHT = 'BY_WEIGHT', _('Per weight (kg)')
        HYBRID = 'HYBRID', _('Hybrid (item + weight)')

    class PayoutMethod(models.TextChoices):
        MOBILE_MONEY = 'MOBILE_MONEY', _('Mobile Money')
        BANK_ACCOUNT = 'BANK_ACCOUNT', _('Bank Account')

    class PayoutStatus(models.TextChoices):
        PAYOUT_SETUP_REQUIRED = 'PAYOUT_SETUP_REQUIRED', _('Payout Setup Required')
        PAYOUT_SETUP_PENDING = 'PAYOUT_SETUP_PENDING', _('Payout Setup Pending')
        PAYOUT_READY = 'PAYOUT_READY', _('Payout Ready')
        PAYOUT_FAILED_RETRYABLE = 'PAYOUT_FAILED_RETRYABLE', _('Payout Failed Retryable')


    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_('name'), max_length=255, db_index=True)
    description = models.TextField(_('description'), blank=True)
    image = models.ImageField(
        _('image'), 
        upload_to='laundries/', 
        blank=True, 
        null=True,
        validators=[validate_file_upload]
    )
    address = models.TextField(_('address'))
    city = models.CharField(_('city'), max_length=100, default='Accra', db_index=True)
    
    # Geospatial Optimization
    latitude = models.DecimalField(
        _('latitude'), max_digits=9, decimal_places=6, db_index=True,
        validators=[validate_latitude],
    )
    longitude = models.DecimalField(
        _('longitude'), max_digits=9, decimal_places=6, db_index=True,
        validators=[validate_longitude],
    )

    phone_number = models.CharField(_('phone number'), max_length=20)
    price_range = models.CharField(_('price range'), max_length=3, choices=PriceRange.choices, default=PriceRange.MEDIUM)
    pricing_model = models.CharField(
        _('pricing model'),
        max_length=10,
        choices=PricingModel.choices,
        default=PricingModel.BY_ITEM,
        db_index=True,
    )
    estimated_delivery_hours = models.IntegerField(_('estimated delivery hours'), default=24)
    delivery_fee = models.DecimalField(_('delivery fee'), max_digits=10, decimal_places=2, default=0.00)
    pickup_fee = models.DecimalField(_('pickup fee'), max_digits=10, decimal_places=2, default=0.00)
    min_order = models.DecimalField(_('minimum order value'), max_digits=10, decimal_places=2, default=0.00)

    # --- Direct settlement (Paystack subaccount) -------------------------
    # When set and enabled, a customer's payment is routed to this laundry's
    # own Paystack subaccount instead of landing in the platform account and
    # waiting for a manual payout.
    #
    # Enabling is deliberately per-laundry and off by default. Splitting money
    # out at charge time means the platform can no longer hold it back if the
    # order goes wrong, so a laundry has to have earned it. Bank details live
    # with Paystack, not here — only the resulting code is stored.
    paystack_subaccount_code = models.CharField(
        _('paystack subaccount code'), max_length=100, blank=True, default=''
    )
    split_payments_enabled = models.BooleanField(
        _('settle payments directly'), default=False, db_index=True
    )
    # Paystack transfer recipient, used to send payouts automatically. Distinct
    # from the subaccount above: a subaccount receives a split at charge time,
    # a recipient receives a transfer afterwards. A laundry on the escrow route
    # needs this one.
    paystack_recipient_code = models.CharField(
        _('paystack recipient code'), max_length=100, blank=True, default=''
    )
    payout_method = models.CharField(
        _('payout method'),
        max_length=20,
        choices=PayoutMethod.choices,
        default=PayoutMethod.MOBILE_MONEY,
    )
    payout_provider = models.CharField(
        _('payout provider'), max_length=50, blank=True, default=''
    )
    payout_phone = models.CharField(
        _('payout phone display'), max_length=30, blank=True, default=''
    )
    payout_phone_normalized = models.CharField(
        _('payout phone normalized'), max_length=30, blank=True, default=''
    )
    payout_account_name = models.CharField(
        _('payout account name'), max_length=150, blank=True, default=''
    )
    payout_status = models.CharField(
        _('payout status'),
        max_length=30,
        choices=PayoutStatus.choices,
        default=PayoutStatus.PAYOUT_SETUP_REQUIRED,
        db_index=True,
    )
    payout_confirmed_at = models.DateTimeField(null=True, blank=True)
    payout_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_payout_laundries',
    )
    recipient_created_at = models.DateTimeField(null=True, blank=True)
    payout_failure_reason = models.TextField(blank=True, default='')

    @property
    def is_payout_ready(self) -> bool:
        return bool(
            self.payout_status == self.PayoutStatus.PAYOUT_READY
            and self.paystack_recipient_code
        )

    @property
    def masked_payout_phone(self) -> str:
        from users.utils.phone import mask_phone_number
        return mask_phone_number(
            self.payout_phone or self.payout_phone_normalized or self.phone_number
        )

    is_featured = models.BooleanField(_('is featured'), default=False, db_index=True)

    is_active = models.BooleanField(_('is active'), default=False, db_index=True)
    vacation_mode = models.BooleanField(_('vacation mode'), default=False, db_index=True)
    service_radius_km = models.DecimalField(_('service radius (km)'), max_digits=5, decimal_places=2, default=5.0)
    service_area_polygon = models.JSONField(_('service area polygon'), null=True, blank=True)
    is_eco_friendly = models.BooleanField(_('is eco-friendly'), default=False, db_index=True)
    ironing_available = models.BooleanField(_('ironing available'), default=False, db_index=True)

    # --- Free Pickup & Delivery Promotion ---
    class PromoFundingSource(models.TextChoices):
        LAUNDRY = 'LAUNDRY', _('Laundry funded (deducted from laundry payout)')
        SIMAME = 'SIMAME', _('Simame funded (platform promotional cost)')

    class PromoScope(models.TextChoices):
        PICKUP_AND_DELIVERY = 'PICKUP_AND_DELIVERY', _('Free pickup & delivery')
        PICKUP_ONLY = 'PICKUP_ONLY', _('Free pickup only')
        DELIVERY_ONLY = 'DELIVERY_ONLY', _('Free delivery only')

    free_delivery_promo_enabled = models.BooleanField(
        _('free pickup & delivery promo enabled'),
        default=False,
        db_index=True,
        help_text=_("Toggle ON to offer free pickup and delivery to customers.")
    )
    promo_start_at = models.DateTimeField(_('promo start date/time'), null=True, blank=True)
    promo_end_at = models.DateTimeField(_('promo end date/time'), null=True, blank=True)
    promo_max_distance_km = models.DecimalField(
        _('promo maximum distance (km)'),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Optional distance ceiling for free delivery (null = up to laundry service radius).")
    )
    promo_funding_source = models.CharField(
        _('promo funding source'),
        max_length=20,
        choices=PromoFundingSource.choices,
        default=PromoFundingSource.LAUNDRY,
        help_text=_("Who pays the rider for the transport the customer gets free. Owners can only choose laundry funded.")
    )
    promo_scope = models.CharField(
        _('promo covers'),
        max_length=24,
        choices=PromoScope.choices,
        default=PromoScope.PICKUP_AND_DELIVERY,
    )
    promo_name = models.CharField(_('promo name'), max_length=80, blank=True, default='')
    promo_min_order_value = models.DecimalField(
        _('promo minimum order (GHS)'),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Optional. Orders whose items total is below this pay normal transport."),
    )
    #: Identifies one campaign. A fresh id is minted each time the promo is
    #: switched on (or restarted after it ended), and the customer push is
    #: de-duplicated on it, so editing a running promo never re-notifies anyone.
    promo_campaign_id = models.UUIDField(null=True, blank=True, editable=False)
    promo_notified_campaign_id = models.UUIDField(null=True, blank=True, editable=False)
    promo_last_notified_at = models.DateTimeField(null=True, blank=True, editable=False)

    @property
    def promo_covers_pickup(self) -> bool:
        return self.promo_scope in (self.PromoScope.PICKUP_AND_DELIVERY, self.PromoScope.PICKUP_ONLY)

    @property
    def promo_covers_delivery(self) -> bool:
        return self.promo_scope in (self.PromoScope.PICKUP_AND_DELIVERY, self.PromoScope.DELIVERY_ONLY)

    def promo_display_label(self) -> str:
        return {
            self.PromoScope.PICKUP_ONLY: 'FREE PICKUP',
            self.PromoScope.DELIVERY_ONLY: 'FREE DELIVERY',
        }.get(self.promo_scope, 'FREE PICKUP & DELIVERY')

    def is_promo_running(self, now=None) -> bool:
        """Switched on and inside its date window. Ignores per-order limits."""
        if not self.free_delivery_promo_enabled:
            return False
        from django.utils import timezone
        now = now or timezone.now()
        if self.promo_start_at and now < self.promo_start_at:
            return False
        if self.promo_end_at and now > self.promo_end_at:
            return False
        return True

    def is_free_delivery_promo_active(self, distance_km=None, items_total=None) -> bool:
        """
        Whether the promo applies to one order: running, within its distance
        ceiling, and meeting its minimum order value when one is set.
        """
        if not self.is_promo_running():
            return False
        from decimal import Decimal, InvalidOperation
        try:
            if distance_km is not None and self.promo_max_distance_km is not None:
                if Decimal(str(distance_km)) > Decimal(str(self.promo_max_distance_km)):
                    return False
            if items_total is not None and self.promo_min_order_value is not None:
                if Decimal(str(items_total)) < Decimal(str(self.promo_min_order_value)):
                    return False
        except (InvalidOperation, TypeError, ValueError):
            return False
        return True

    
    status = models.CharField(
        _('approval status'),
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True
    )
    
    # Approval Timestamps
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    changes_requested_at = models.DateTimeField(null=True, blank=True)
    # When the owner (re)submitted for review; drives approval-duration analytics.
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Reason attached to the latest reject / request-changes / suspend decision.
    status_reason = models.TextField(_('status reason'), blank=True, default='')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_laundries',
    )

    # Deactivation (Soft-Delete)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivation_reason = models.TextField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_laundries',
        limit_choices_to={'role': 'OWNER'}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Laundry')
        verbose_name_plural = _('Laundries')
        ordering = ['-created_at']
        # Build indexes list conditionally
        indexes = [
            models.Index(fields=['is_featured', 'is_active']),
            models.Index(fields=['price_range']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Sync PointField with latitude/longitude for backward compatibility (only if PostGIS is enabled)
        if USE_POSTGIS and self.latitude and self.longitude:
            try:
                # pyre-ignore[missing-module]
                from django.contrib.gis.geos import Point
                self.location = Point(float(self.longitude), float(self.latitude))
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Could not sync PostGIS point for laundry %s: %s", self.pk, exc
                )
        if self.payout_phone:
            from users.utils.phone import normalize_phone, PhoneValidationError
            try:
                self.payout_phone_normalized = normalize_phone(self.payout_phone)
            except PhoneValidationError:
                pass
        started_campaign = self._start_promo_campaign_if_new(kwargs)
        super().save(*args, **kwargs)
        if started_campaign or (
            self.free_delivery_promo_enabled
            and self.promo_campaign_id
            and self.promo_campaign_id != self.promo_notified_campaign_id
        ):
            from django.db import transaction
            from laundries.services.promo_notifications import announce_promo_campaign
            laundry_id, campaign_id = self.pk, self.promo_campaign_id
            transaction.on_commit(lambda: announce_promo_campaign(laundry_id, campaign_id))

    def _start_promo_campaign_if_new(self, save_kwargs) -> bool:
        """
        Mint a new campaign id when the promo is switched on, or restarted after
        its previous window ended. Edits to a running promo keep the same id.
        """
        if not self.free_delivery_promo_enabled:
            return False
        is_new = self.promo_campaign_id is None
        if not is_new and self.pk and not self._state.adding:
            previous = (
                Laundry.objects.filter(pk=self.pk)
                .values('free_delivery_promo_enabled', 'promo_end_at')
                .first()
            )
            if previous is not None:
                from django.utils import timezone
                ended = previous['promo_end_at'] is not None and previous['promo_end_at'] < timezone.now()
                is_new = (not previous['free_delivery_promo_enabled']) or ended
        if not is_new:
            return False
        self.promo_campaign_id = uuid.uuid4()
        update_fields = save_kwargs.get('update_fields')
        if update_fields is not None:
            save_kwargs['update_fields'] = list(set(update_fields) | {'promo_campaign_id'})
        return True

    # We keep it as a normal field but handle the case where GDAL is missing
    # in the migrations or local environment.
    location = None
    if USE_POSTGIS:
        try:
             # pyre-ignore[missing-module]
             from django.contrib.gis.db import models as gis_models
             location = gis_models.PointField(_('location'), srid=4326, null=True, blank=True, db_index=True)
        except Exception:
             import logging
             logging.error("GIS models not available for Laundry.location")


class OwnerAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='audit_logs',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='laundry_audit_logs',
    )
    action = models.CharField(_('action'), max_length=100, db_index=True)
    details = models.JSONField(_('details'), default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Owner Audit Log')
        verbose_name_plural = _('Owner Audit Logs')
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} by {self.actor} at {self.timestamp}"

