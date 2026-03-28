# pyre-ignore[missing-module]
from rest_framework import serializers
from decimal import Decimal
# pyre-ignore[missing-module]
from ordering.models import LaunderableItem, BookingSlot, Order, OrderItem
from .lifecycle import OrderStatusHistorySerializer

class LaunderableItemSerializer(serializers.ModelSerializer):
    item_category_name = serializers.CharField(source='item_category.name', read_only=True)

    class Meta:
        model = LaunderableItem
        fields = ['id', 'name', 'item_category', 'item_category_name', 'image', 'is_active']

class BookingSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingSlot
        fields = ['id', 'start_time', 'end_time', 'is_available', 'max_bookings', 'current_bookings']

class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['id', 'item', 'service_type', 'name', 'quantity', 'price']
        read_only_fields = ['id', 'name', 'price']

class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    laundryName = serializers.CharField(source='laundry.name', read_only=True)
    history = OrderStatusHistorySerializer(source='status_history', many=True, read_only=True)
    
    # Live Tracking Fields
    van_latitude = serializers.SerializerMethodField()
    van_longitude = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'order_no', 'laundryName', 
            'status', 'payment_status', 'estimated_price', 'final_price', 
            'pickup_date', 'delivery_date', 
            'pickup_address', 'pickup_lat', 'pickup_lng',
            'delivery_address', 'delivery_lat', 'delivery_lng',
            'address', 'pricing_method', 'estimated_weight', 
            'actual_weight', 'price_per_kg_snapshot',
            'special_instructions', 'items', 'history',
            'van_latitude', 'van_longitude', 'created_at'
        ]

    def _get_latest_tracking_coord(self, obj, coord_type):
        """Helper to get latest coordinate from TrackingLog when out for delivery."""
        if obj.status == Order.Status.OUT_FOR_DELIVERY:
            # We import here to avoid circular dependencies if any
            from logistics.models import TrackingLog
            log = TrackingLog.objects.filter(order=obj).order_by('-timestamp').first()
            if log:
                return getattr(log, coord_type)
        return None

    def get_van_latitude(self, obj):
        return self._get_latest_tracking_coord(obj, 'latitude')

    def get_van_longitude(self, obj):
        return self._get_latest_tracking_coord(obj, 'longitude')

class OrderCreateSerializer(serializers.ModelSerializer):
    # pyre-ignore[missing-module]
    items = OrderItemSerializer(many=True, required=False)
    coupon_code = serializers.CharField(required=False, write_only=True)

    # Accept GPS coords and payment_method from frontend
    pickup_lat = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    pickup_lng = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    delivery_lat = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    delivery_lng = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    # payment_method accepted for future use but not saved on Order
    payment_method = serializers.CharField(required=False, write_only=True)

    class Meta:
        model = Order
        fields = [
            'laundry', 'pickup_date', 
            'pickup_address', 'pickup_lat', 'pickup_lng',
            'delivery_address', 'delivery_lat', 'delivery_lng',
            'special_instructions', 'items', 'coupon_code',
            'payment_method', 'pricing_method', 'estimated_weight'
        ]

    def validate(self, data):
        laundry = data.get('laundry')
        pricing_method = data.get('pricing_method', 'PER_ITEM')
        
        if laundry and (laundry.status != 'APPROVED' or not laundry.is_active):
            raise serializers.ValidationError("This laundry is not approved or is currently inactive.")
            
        # Pricing Method Validation
        if pricing_method == 'PER_KG':
            if not laundry.price_per_kg:
                raise serializers.ValidationError(f"{laundry.name} does not support weight-based pricing.")
            if not data.get('estimated_weight'):
                raise serializers.ValidationError({"estimated_weight": "Estimated weight is required for Per Kg orders."})
        else:
            if not data.get('items'):
                raise serializers.ValidationError({"items": "Items are required for Per Item orders."})

        coupon_code = data.get('coupon_code')
        if coupon_code:
            # pyre-ignore[missing-module]
            from ..models.coupons import Coupon
            try:
                coupon = Coupon.objects.get(code=coupon_code)
                user = self.context['request'].user
                is_valid, error = coupon.is_valid(user=user, laundry_id=laundry.id if laundry else None)
                if not is_valid:
                    raise serializers.ValidationError({"coupon_code": error})
                data['coupon_obj'] = coupon
            except Coupon.DoesNotExist:
                raise serializers.ValidationError({"coupon_code": "Invalid coupon code."})
                
        return data

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        coupon_obj = validated_data.pop('coupon_obj', None)
        pricing_method = validated_data.get('pricing_method', 'PER_ITEM')
        laundry = validated_data.get('laundry')
        
        # Discard frontend-only fields
        validated_data.pop('payment_method', None)
        validated_data.pop('coupon_code', None)
        user = self.context['request'].user
        
        # Snapshot price per kg if applicable
        if pricing_method == 'PER_KG':
            validated_data['price_per_kg_snapshot'] = laundry.price_per_kg
        
        order = Order.objects.create(
            user=user,
            estimated_price=0, # Placeholder
            final_price=0,     # Placeholder
            coupon=coupon_obj,
            **validated_data
        )
        
        if pricing_method == 'PER_ITEM':
            # pyre-ignore[missing-module]
            from laundries.models.service import LaundryService

            for item_data in items_data:
                item_instance = item_data.get('item')
                service_type_instance = item_data.get('service_type')
                quantity = item_data.get('quantity', 1)
                
                if not service_type_instance:
                     raise serializers.ValidationError(f"Service type is required for {item_instance.name}")

                try:
                    l_service = LaundryService.objects.get(
                        laundry=order.laundry,
                        item=item_instance,
                        service_type=service_type_instance
                    )
                    if not l_service.is_available:
                          raise serializers.ValidationError(f"{item_instance.name} is not available for {service_type_instance.name}.")
                    item_price = l_service.price
                except LaundryService.DoesNotExist:
                    raise serializers.ValidationError(f"Laundry does not offer {service_type_instance.name} for {item_instance.name}")

                OrderItem.objects.create(
                    order=order, 
                    item=item_instance,
                    service_type=service_type_instance,
                    name=item_instance.name,
                    quantity=quantity,
                    price=item_price
                )
            
        # Now calculate real total
        # pyre-ignore[missing-module]
        from ..services.finance_service import FinanceService
        price_breakdown = FinanceService.calculate_price_breakdown(order, coupon=coupon_obj)
        total = Decimal(price_breakdown['total'])
        order.estimated_price = total
        # For PER_ITEM, estimated and final are the same at start
        if pricing_method == 'PER_ITEM':
            order.final_price = total
        order.save()
        
        # Record Coupon Usage
        if coupon_obj:
            # pyre-ignore[missing-module]
            from ..models.coupons import CouponUsage
            CouponUsage.objects.create(user=user, coupon=coupon_obj, order=order)
            coupon_obj.current_usage += 1
            coupon_obj.save()
            
        return order
