"""Serializers for owner-managed pricing (per-item catalog + weight tariff)."""
# pyre-ignore[missing-module]
from rest_framework import serializers

from ..models.pricing import (
    LaundryPricingItem, LaundryWeightPricing,
    PricingCatalogVersion, ScheduledPriceChange, DeliveryZonePricing
)


class LaundryPricingItemSerializer(serializers.ModelSerializer):
    imageUrl = serializers.SerializerMethodField()

    class Meta:
        model = LaundryPricingItem
        fields = [
            'id', 'item_name', 'category', 'image', 'imageUrl', 'unit_price',
            'is_active', 'display_order', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'imageUrl', 'created_at', 'updated_at']

    def get_imageUrl(self, obj) -> str | None:
        if not obj.image:
            return None
        request = self.context.get('request')
        try:
            url = obj.image.url
        except (ValueError, AttributeError):
            return None
        return request.build_absolute_uri(url) if request else url


class PricingItemBulkUpdateRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    item_name = serializers.CharField(max_length=120, required=False)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True)
    unit_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False
    )
    is_active = serializers.BooleanField(required=False)
    display_order = serializers.IntegerField(min_value=0, required=False)


class PricingItemBulkUpdateSerializer(serializers.Serializer):
    items = PricingItemBulkUpdateRowSerializer(many=True)


class PricingItemReorderRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    display_order = serializers.IntegerField(min_value=0)


class PricingItemReorderSerializer(serializers.Serializer):
    items = PricingItemReorderRowSerializer(many=True)


class LaundryWeightPricingSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaundryWeightPricing
        fields = [
            'id', 'base_price_per_kg', 'minimum_charge', 'minimum_order_weight_kg',
            'rounding_strategy', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PricingCatalogVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingCatalogVersion
        fields = ['id', 'version_number', 'items_data', 'created_at']
        read_only_fields = ['id', 'created_at']


class ScheduledPriceChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledPriceChange
        fields = ['id', 'effective_at', 'pricing_data', 'is_applied', 'created_at']
        read_only_fields = ['id', 'is_applied', 'created_at']


class DeliveryZonePricingSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZonePricing
        fields = ['id', 'min_distance_km', 'max_distance_km', 'delivery_fee', 'pickup_fee']
        read_only_fields = ['id']

