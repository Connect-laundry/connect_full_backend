# pyre-ignore[missing-module]
from rest_framework import serializers

# pyre-ignore[missing-module]
from ..models.service import LaundryService


class LaundryServiceSerializer(serializers.ModelSerializer):
    """
    Serializer for managing laundry services.
    Supports administrative creation and listing.
    """

    # Read-only fields for frontend display
    itemName = serializers.CharField(source="item.name", read_only=True)
    serviceType = serializers.CharField(source="service_type.name", read_only=True)
    itemCategory = serializers.CharField(
        source="item.item_category.name", read_only=True
    )

    class Meta:
        model = LaundryService
        fields = (
            "id",
            "laundry",
            "item",
            "service_type",
            "price",
            "estimated_duration",
            "is_available",
            "itemName",
            "serviceType",
            "itemCategory",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        # Additional validation can go here
        return attrs
