# pyre-ignore[missing-module]
from rest_framework import serializers
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
# pyre-ignore[missing-module]
from ordering.models import Order
# pyre-ignore[missing-module]
from laundries.models.service import LaundryService

class DashboardOrderSerializer(serializers.ModelSerializer):
    payment_state = serializers.SerializerMethodField()
    amount_due = serializers.SerializerMethodField()
    amount_collected = serializers.SerializerMethodField()
    cash_collected_at = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source='user.get_full_name', read_only=True)
    # Customer contact + pickup location so the laundry owner can reach the
    # customer and locate the pickup. Phone is now always present because the
    # customer app captures it before the first order is created.
    customer_phone = serializers.CharField(source='user.phone', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_no', 'customer_name', 'customer_phone', 'status',
            'status_display', 'total_amount', 'created_at',
            'pickup_date', 'delivery_date', 'pickup_address',
            'pricing_mode', 'payment_method', 'payment_status', 'payment_state',
            'amount_due', 'amount_collected', 'cash_collected_at',
        ]

    @extend_schema_field(OpenApiTypes.STR)
    def get_payment_state(self, obj):
        from ordering.serializers.order import OrderDetailSerializer
        return OrderDetailSerializer(obj).get_payment_state(obj)

    @extend_schema_field(OpenApiTypes.STR)
    def get_amount_due(self, obj):
        from ordering.serializers.order import OrderDetailSerializer
        return OrderDetailSerializer(obj).get_amount_due(obj)

    @extend_schema_field(OpenApiTypes.STR)
    def get_amount_collected(self, obj):
        from ordering.serializers.order import OrderDetailSerializer
        return OrderDetailSerializer(obj).get_amount_collected(obj)

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_cash_collected_at(self, obj):
        from ordering.serializers.order import OrderDetailSerializer
        return OrderDetailSerializer(obj).get_cash_collected_at(obj)

class DashboardStatsSerializer(serializers.Serializer):
    pending_count = serializers.IntegerField()
    confirmed_count = serializers.IntegerField()
    picked_up_count = serializers.IntegerField()
    delivered_count = serializers.IntegerField()
    total_orders = serializers.IntegerField()
    revenue_today = serializers.DecimalField(max_digits=12, decimal_places=2)
    revenue_this_month = serializers.DecimalField(max_digits=12, decimal_places=2)
    average_order_value = serializers.DecimalField(max_digits=12, decimal_places=2)
    most_popular_items = serializers.JSONField()
    repeat_customer_rate = serializers.FloatField()
    pending_pickups = serializers.IntegerField()
    pending_deliveries = serializers.IntegerField()
    average_turnaround_time = serializers.FloatField()


class DashboardEarningsSerializer(serializers.Serializer):
    today = serializers.DecimalField(max_digits=12, decimal_places=2)
    this_week = serializers.DecimalField(max_digits=12, decimal_places=2)
    this_month = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)

class ServiceStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaundryService
        fields = ['is_available']
