# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from ..models.coupons import Coupon, CouponUsage

class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            'id', 'code', 'discount_type', 'discount_value', 
            'min_order_value', 'valid_from', 'valid_to', 
            'max_usage', 'current_usage', 'user_limit', 'is_active'
        ]

class CouponValidationSerializer(serializers.Serializer):
    code = serializers.CharField(required=True)
    laundry_id = serializers.UUIDField(required=True)
    items_total = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)
