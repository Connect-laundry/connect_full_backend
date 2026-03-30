from rest_framework import serializers


class CustomerSummarySerializer(serializers.Serializer):
    """Aggregated customer stats for the CRM view."""

    user_id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone = serializers.CharField()
    order_count = serializers.IntegerField()
    total_spent = serializers.DecimalField(max_digits=12, decimal_places=2)
    last_order_date = serializers.DateTimeField()


class CustomerProfileSerializer(serializers.Serializer):
    """Detailed customer profile with order history."""

    user_id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone = serializers.CharField()
    order_count = serializers.IntegerField()
    total_spent = serializers.DecimalField(max_digits=12, decimal_places=2)
    orders = serializers.ListField(child=serializers.DictField())
