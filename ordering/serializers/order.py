# pyre-ignore[missing-module]
from rest_framework import serializers
from ordering.models import LaunderableItem, BookingSlot, Order, OrderItem

class LaunderableItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaunderableItem
        fields = ['id', 'name', 'category', 'base_price', 'image', 'is_active']

class BookingSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingSlot
        fields = ['id', 'start_time', 'end_time', 'is_available', 'max_bookings', 'current_bookings']

class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['id', 'item', 'name', 'quantity', 'price']
        read_only_fields = ['id']

class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    laundryName = serializers.CharField(source='laundry.name', read_only=True)
    serviceName = serializers.CharField(source='service_type.name', read_only=True)
    
    class Meta:
        model = Order
        fields = [
            'id', 'order_no', 'laundryName', 'serviceName', 
            'status', 'payment_status', 'total_amount', 
            'pickup_date', 'delivery_date', 'address', 
            'special_instructions', 'items', 'created_at'
        ]

class OrderCreateSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = [
            'laundry', 'service_type', 'pickup_date', 
            'address', 'special_instructions', 'items'
        ]

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user = self.context['request'].user
        
        # Calculate total amount
        total_amount = sum(item['price'] * item['quantity'] for item in items_data)
        
        order = Order.objects.create(
            user=user,
            total_amount=total_amount,
            **validated_data
        )
        
        for item_data in items_data:
            OrderItem.objects.create(order=order, **item_data)
            
        return order
