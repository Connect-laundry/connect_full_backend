# pyre-ignore[missing-module]
from rest_framework import serializers
from decimal import Decimal
# pyre-ignore[missing-module]
from ordering.models import LaunderableItem, BookingSlot, Order, OrderItem

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
    
    class Meta:
        model = Order
        fields = [
            'id', 'order_no', 'laundryName', 
            'status', 'payment_status', 'total_amount', 
            'pickup_date', 'delivery_date', 
            'pickup_address', 'delivery_address', 'address', 
            'special_instructions', 'items', 'created_at'
        ]

class OrderCreateSerializer(serializers.ModelSerializer):
    # pyre-ignore[missing-module]
    items = OrderItemSerializer(many=True)
    coupon_code = serializers.CharField(required=False, write_only=True)

    class Meta:
        model = Order
        fields = [
            'laundry', 'pickup_date', 
            'pickup_address', 'delivery_address',
            'special_instructions', 'items', 'coupon_code'
        ]

    def validate(self, data):
        laundry = data.get('laundry')
        if laundry and (laundry.status != 'APPROVED' or not laundry.is_active):
            raise serializers.ValidationError("This laundry is not approved or is currently inactive.")
            
        coupon_code = data.get('coupon_code')
        if coupon_code:
            # pyre-ignore[missing-module]
            from ..models.coupons import Coupon
            try:
                coupon = Coupon.objects.get(code=coupon_code)
                # We can't fully validate usage limits here without the user, 
                # but we'll do it in create() or a preliminary check if user is in context
                user = self.context['request'].user
                is_valid, error = coupon.is_valid(user=user, laundry_id=laundry.id if laundry else None)
                if not is_valid:
                    raise serializers.ValidationError({"coupon_code": error})
                data['coupon_obj'] = coupon
            except Coupon.DoesNotExist:
                raise serializers.ValidationError({"coupon_code": "Invalid coupon code."})
                
        return data

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        coupon_obj = validated_data.pop('coupon_obj', None)
        user = self.context['request'].user
        
        # Temporary order object to pass to FinanceService
        temp_order = Order(
            user=user,
            laundry=validated_data.get('laundry')
        )
        # We need to add items to temp_order for FinanceService
        # But FinanceService uses DB queries. So we must create the order first then update amount?
        # Or better: FinanceService should take items_total directly if needed.
        # Let's create the order with total=0 first, then calculate.
        
        order = Order.objects.create(
            user=user,
            total_amount=0, # Placeholder
            coupon=coupon_obj,
            **validated_data
        )
        
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
        order.total_amount = Decimal(price_breakdown['total'])
        order.save()
        
        # Record Coupon Usage
        if coupon_obj:
            # pyre-ignore[missing-module]
            from ..models.coupons import CouponUsage
            CouponUsage.objects.create(user=user, coupon=coupon_obj, order=order)
            coupon_obj.current_usage += 1
            coupon_obj.save()
            
        return order
