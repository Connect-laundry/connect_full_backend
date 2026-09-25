"""Serializers for AI-assisted price-list import.

Response fields are additive over the original contract (``item_name``,
``suggested_price``, ``category``, ``confidence``, ``is_selected``,
``status``/``READY``), so existing web-app code keeps working.
"""
# pyre-ignore[missing-module]
from rest_framework import serializers

from ..models.price_import import PriceListDraftItem, PriceListImportJob


class PriceListDraftItemSerializer(serializers.ModelSerializer):
    matched_item = serializers.SerializerMethodField()

    class Meta:
        model = PriceListDraftItem
        fields = [
            'id', 'position', 'item_name', 'raw_name', 'variant', 'category',
            'pricing_method', 'suggested_price', 'price_per_kg',
            'surcharge_type', 'surcharge_amount', 'source_text',
            'review_state', 'warnings', 'confidence', 'match_type', 'matched_item',
            'is_selected',
        ]
        read_only_fields = fields

    def get_matched_item(self, obj) -> dict | None:
        item = obj.matched_item
        if item is None:
            return None
        return {'id': str(item.id), 'item_name': item.item_name,
                'unit_price': str(item.unit_price), 'category': item.category}


class PriceListImportJobSerializer(serializers.ModelSerializer):
    draft_items = PriceListDraftItemSerializer(many=True, read_only=True)
    review_required = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = PriceListImportJob
        fields = [
            'id', 'status', 'review_required', 'provider', 'error', 'error_code',
            'currency', 'document_warnings', 'summary', 'served_from_cache',
            'draft_items', 'created_at', 'updated_at', 'completed_at', 'confirmed_at',
        ]
        read_only_fields = fields

    def get_review_required(self, obj) -> bool:
        return obj.status == PriceListImportJob.Status.READY

    def get_summary(self, obj) -> dict:
        items = list(obj.draft_items.all())
        states = [i.review_state for i in items]
        return {
            'total': len(items),
            'looks_good': states.count('LOOKS_GOOD'),
            'please_check': states.count('CHECK'),
            'could_not_read': states.count('UNREADABLE'),
            'possible_matches': sum(1 for i in items if i.match_type != 'NONE'),
        }


class PriceImportCreateSerializer(serializers.Serializer):
    source_image = serializers.FileField(help_text='JPEG, PNG or WebP, up to PRICE_LIST_UPLOAD_MAX_MB.')


class ConfirmDraftRowSerializer(serializers.Serializer):
    """One reviewed row. Legacy rows ({item_name, unit_price, category}) are
    still accepted and treated as CREATE / PER_ITEM."""
    draft_id = serializers.UUIDField(required=False, allow_null=True)
    action = serializers.ChoiceField(choices=['CREATE', 'UPDATE', 'IGNORE'], required=False)
    item_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True, default='')
    pricing_method = serializers.ChoiceField(choices=['PER_ITEM', 'PER_KG', 'UNKNOWN'], required=False)
    unit_price = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    price_per_kg = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    existing_item_id = serializers.UUIDField(required=False, allow_null=True)


class PriceImportConfirmSerializer(serializers.Serializer):
    items = ConfirmDraftRowSerializer(many=True)
    currency_confirmed = serializers.BooleanField(required=False, default=False)
