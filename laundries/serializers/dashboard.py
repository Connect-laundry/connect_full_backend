# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from ordering.models import Order
# pyre-ignore[missing-module]
from ..models.service import Service

class DashboardOrderSerializer(serializers.ModelSerializer):
    customer = serializers.SerializerMethodField()
    estimated_delivery_time = serializers.CharField(read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'customer', 'status', 'total_amount', 
            'created_at', 'estimated_delivery_time'
        ]

    def get_customer(self, obj):
        return {
            "id": obj.user.id,
            "name": obj.user.get_full_name()
        }

class ServicePriceUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'base_price', 'is_active']
        extra_kwargs = {
            'base_price': {'required': True},
            'is_active': {'required': True}
        }

    def validate_base_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Price must be greater than zero.")
        return value

class BulkServiceUpdateSerializer(serializers.Serializer):
    # pyre-ignore[missing-module]
    services = ServicePriceUpdateSerializer(many=True)
