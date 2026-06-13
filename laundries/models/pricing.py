"""Owner-defined pricing structures.

Two complementary models, selected per-laundry via ``Laundry.pricing_model``:

* ``LaundryPricingItem`` — relational per-item catalog (``BY_ITEM`` / ``HYBRID``).
* ``LaundryWeightPricing`` — weight (kg) tariff (``BY_WEIGHT`` / ``HYBRID``).

These are intentionally distinct from the global ``LaundryService`` bridge
(which maps the shared ``LaunderableItem`` catalog to a laundry). The owner
onboarding flow lets owners declare a free-form, self-managed price list without
needing entries to exist in the global catalog first.
"""
import uuid

# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.core.validators import MinValueValidator
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

from ..utils.validators import validate_file_upload


class LaundryPricingItem(models.Model):
    """A single owner-defined priced item (e.g. "Shirt", "Duvet")."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='pricing_items',
    )
    item_name = models.CharField(_('item name'), max_length=120)
    # Free-form category label (e.g. "Tops", "Bedding"); not tied to the global
    # Category table so owners can organise their list however they like.
    category = models.CharField(_('category'), max_length=80, blank=True, default='')
    image = models.ImageField(
        _('image'),
        upload_to='pricing_items/',
        blank=True,
        null=True,
        validators=[validate_file_upload],
    )
    unit_price = models.DecimalField(
        _('unit price'),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField(_('is active'), default=True)
    display_order = models.PositiveIntegerField(_('display order'), default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Laundry Pricing Item')
        verbose_name_plural = _('Laundry Pricing Items')
        ordering = ['display_order', 'item_name']
        # An owner cannot list the same item name twice within one laundry.
        unique_together = ('laundry', 'item_name')
        indexes = [
            models.Index(fields=['laundry', 'is_active']),
            models.Index(fields=['laundry', 'display_order']),
        ]

    def __str__(self):
        return f"{self.item_name} - {self.unit_price} ({self.laundry_id})"


class LaundryWeightPricing(models.Model):
    """Weight-based tariff for a laundry (one row per laundry)."""

    class RoundingStrategy(models.TextChoices):
        NONE = 'NONE', _('No rounding')
        UP_0_5_KG = 'UP_0_5_KG', _('Round up to nearest 0.5 kg')
        UP_1_KG = 'UP_1_KG', _('Round up to nearest 1 kg')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.OneToOneField(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='weight_pricing',
    )
    base_price_per_kg = models.DecimalField(
        _('base price per kg'),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    minimum_charge = models.DecimalField(
        _('minimum charge'),
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    minimum_order_weight_kg = models.DecimalField(
        _('minimum order weight (kg)'),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    rounding_strategy = models.CharField(
        _('rounding strategy'),
        max_length=12,
        choices=RoundingStrategy.choices,
        default=RoundingStrategy.NONE,
    )
    is_active = models.BooleanField(_('is active'), default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Laundry Weight Pricing')
        verbose_name_plural = _('Laundry Weight Pricing')

    def __str__(self):
        return f"{self.base_price_per_kg}/kg ({self.laundry_id})"
