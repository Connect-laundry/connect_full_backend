"""Serializers for owner-managed pricing (per-item catalog + weight tariff)."""
# pyre-ignore[missing-module]
from rest_framework import serializers

from utils.media import (
    OptionalMediaWriteMixin,
    SafeMediaModelSerializer,
    safe_media_url,
)

from ..models.pricing import (
    BasketDefinition, LaundryPricingItem, LaundryWeightPricing,
    PricingCatalogVersion, ScheduledPriceChange, DeliveryZonePricing
)


class BasketDefinitionSerializer(serializers.ModelSerializer):
    inclusions = serializers.ListField(child=serializers.CharField(max_length=500), max_length=50, required=False)
    exclusions = serializers.ListField(child=serializers.CharField(max_length=500), max_length=50, required=False)

    class Meta:
        model = BasketDefinition
        fields = ['id', 'name', 'description', 'capacity_description', 'max_weight_kg', 'max_pieces',
                  'dimensions', 'inclusions', 'exclusions', 'overfill_policy', 'container_requirements',
                  'is_active', 'revision', 'created_at', 'updated_at']
        read_only_fields = ['id', 'revision', 'created_at', 'updated_at']

    def validate(self, attrs):
        from ordering.services.catalog_pricing import enabled
        if not enabled():
            raise serializers.ValidationError('Basket pricing is not enabled yet.')
        return attrs


class LaundryPricingItemSerializer(OptionalMediaWriteMixin, SafeMediaModelSerializer):
    imageUrl = serializers.SerializerMethodField()
    basket_details = BasketDefinitionSerializer(source="basket", read_only=True)


    class Meta:
        model = LaundryPricingItem
        fields = [
            'id', 'item_name', 'category', 'image', 'imageUrl', 'unit_price',
            'is_active', 'display_order', 'created_at', 'updated_at',
            'service_type', 'pricing_basis', 'description', 'currency', 'pricing_rules', 'revision', 'basket', 'basket_details',
        ]
        read_only_fields = ['id', 'imageUrl', 'created_at', 'updated_at', 'revision', 'basket_details']
        # Item photo is optional: a storage outage must not 500 a price edit.
        optional_media_fields = ['image']

    def validate(self, attrs):
        from ordering.services.catalog_pricing import validate_offering
        validate_offering(attrs, self.instance)
        basket = attrs.get('basket', getattr(self.instance, 'basket', None))
        request = self.context.get('request')
        if basket and (not request or basket.laundry.owner_id != request.user.pk):
            raise serializers.ValidationError({'basket': 'Choose a basket belonging to your laundry.'})
        if basket and self.instance and basket.laundry_id != self.instance.laundry_id:
            raise serializers.ValidationError({'basket': 'Basket and service must belong to the same laundry.'})
        basis = attrs.get('pricing_basis', getattr(self.instance, 'pricing_basis', 'PER_ITEM'))
        if basis == 'WEIGHT_TIER' and request:
            rules = attrs.get('pricing_rules', getattr(self.instance, 'pricing_rules', {}))
            service_type = attrs.get('service_type', getattr(self.instance, 'service_type', 'CUSTOM'))
            category = attrs.get('category', getattr(self.instance, 'category', ''))
            peers = LaundryPricingItem.objects.filter(laundry__owner=request.user, pricing_basis='WEIGHT_TIER', service_type=service_type, category=category, is_active=True)
            if self.instance:
                peers = peers.exclude(pk=self.instance.pk)
            from decimal import Decimal
            for peer in peers.only('pricing_rules'):
                other = peer.pricing_rules
                if Decimal(str(rules['weight_min'])) < Decimal(str(other['weight_max'])) and Decimal(str(other['weight_min'])) < Decimal(str(rules['weight_max'])):
                    raise serializers.ValidationError({'pricing_rules': 'This interval overlaps an existing service tier.'})
        return attrs

    def get_imageUrl(self, obj) -> str | None:
        return safe_media_url(obj.image, self.context.get('request'))


class PricingItemBulkUpdateRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    item_name = serializers.CharField(max_length=120, required=False)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True)
    unit_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False
    )
    is_active = serializers.BooleanField(required=False)
    display_order = serializers.IntegerField(min_value=0, required=False)
    service_type = serializers.ChoiceField(choices=LaundryPricingItem.ServiceType.choices, required=False)
    pricing_basis = serializers.ChoiceField(choices=LaundryPricingItem.PricingBasis.choices, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    currency = serializers.CharField(max_length=3, required=False)
    pricing_rules = serializers.JSONField(required=False)
    basket = serializers.UUIDField(required=False, allow_null=True)


class PricingItemBulkUpdateSerializer(serializers.Serializer):
    items = PricingItemBulkUpdateRowSerializer(many=True, allow_empty=False, max_length=100)


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
