"""Serializers for AI-assisted price-list import."""
# pyre-ignore[missing-module]
from rest_framework import serializers

from ..models.price_import import PriceListDraftItem, PriceListImportJob


class PriceListDraftItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceListDraftItem
        fields = ['id', 'item_name', 'suggested_price', 'category', 'confidence', 'is_selected']
        read_only_fields = ['id', 'confidence']


class PriceListImportJobSerializer(serializers.ModelSerializer):
    draft_items = PriceListDraftItemSerializer(many=True, read_only=True)

    class Meta:
        model = PriceListImportJob
        fields = [
            'id', 'status', 'provider', 'error', 'draft_items',
            'created_at', 'updated_at', 'confirmed_at',
        ]
        read_only_fields = fields


class PriceImportCreateSerializer(serializers.Serializer):
    source_image = serializers.ImageField()


class ConfirmDraftRowSerializer(serializers.Serializer):
    """A single reviewed row the owner wants to persist as a pricing item.

    The owner may correct the extracted name/price before confirming.
    """
    item_name = serializers.CharField(max_length=120)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True, default='')


class PriceImportConfirmSerializer(serializers.Serializer):
    items = ConfirmDraftRowSerializer(many=True)
