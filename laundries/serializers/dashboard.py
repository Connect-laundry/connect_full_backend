# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from ordering.models import Order
# pyre-ignore[missing-module]
from laundries.models.service import LaundryService

class DashboardOrderSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='user.get_full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_no', 'customer_name', 'status', 
            'status_display', 'total_amount', 'created_at', 
            'pickup_date', 'delivery_date'
        ]

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
