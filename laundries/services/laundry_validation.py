from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_laundry_ready_for_business(laundry):
    """
    Shared business logic for ensuring a laundry is configured and ready for orders.
    Used by models, serializers, and views to ensure consistency across the system.
    """
    errors = {}

    # 1. PER_ITEM Validation
    if "PER_ITEM" in laundry.pricing_methods:
        # Check if any associated LaundryService records exist
        if not laundry.laundry_services.exists():
            errors['pricing_methods'] = _(
                "At least one item/service must be added to the catalog before "
                "enabling Per Item pricing on an active store."
            )

    # 2. PER_KG Validation
    if "PER_KG" in laundry.pricing_methods:
        if not laundry.price_per_kg or laundry.price_per_kg <= 0:
            errors['price_per_kg'] = _(
                "Price per kg must be greater than zero when Per Kg pricing is enabled."
            )

    if errors:
        raise ValidationError(errors)
