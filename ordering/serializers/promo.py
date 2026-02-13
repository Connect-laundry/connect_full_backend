from rest_framework import serializers

class CouponValidateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    order_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    laundry_id = serializers.UUIDField()

class CouponResponseSerializer(serializers.Serializer):
    discount_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    final_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    expires_at = serializers.DateTimeField()
