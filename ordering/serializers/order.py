# pyre-ignore[missing-module]
from rest_framework import serializers
from decimal import Decimal
# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from ordering.models import LaunderableItem, BookingSlot, Order, OrderItem
from laundries.models.category import Category
from laundries.models.laundry import Laundry

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


class OrderItemCreateSerializer(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=LaunderableItem.objects.filter(is_active=True))
    service_type = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(type=Category.CategoryType.SERVICE_TYPE)
    )
    quantity = serializers.IntegerField(min_value=1, max_value=99)

class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    laundryName = serializers.CharField(source='laundry.name', read_only=True)
    price_breakdown = serializers.SerializerMethodField()
    
    class Meta:
        model = Order
        fields = [
            'id', 'order_no', 'laundryName', 
            'status', 'payment_status', 'total_amount', 
            'price_breakdown',
            'pickup_date', 'delivery_date', 
            'pickup_address', 'pickup_lat', 'pickup_lng',
            'delivery_address', 'delivery_lat', 'delivery_lng',
            'address', 
            'special_instructions', 'items', 'created_at'
        ]

    def get_price_breakdown(self, obj):
        from ..services.finance_service import FinanceService

        return FinanceService.calculate_price_breakdown(obj, coupon=obj.coupon)

class OrderCreateSerializer(serializers.ModelSerializer):
    items = OrderItemCreateSerializer(many=True, allow_empty=False)
    laundry = serializers.PrimaryKeyRelatedField(queryset=Laundry.objects.all())
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
            'payment_method',
        ]

    def to_internal_value(self, data):
        if isinstance(data, dict):
            allowed_fields = set(self.fields)
            unsupported_fields = sorted(set(data) - allowed_fields)
            if unsupported_fields:
                raise serializers.ValidationError({
                    "non_field_errors": [
                        f"Unsupported booking fields: {', '.join(unsupported_fields)}."
                    ]
                })
        return super().to_internal_value(data)

    def validate(self, data):
        laundry = data.get('laundry')
        if laundry and (laundry.status != 'APPROVED' or not laundry.is_active):
            raise serializers.ValidationError({"laundry": "This laundry is not approved or is currently inactive."})

        pickup_address = str(data.get('pickup_address') or '').strip()
        delivery_address = str(data.get('delivery_address') or '').strip()
        if not pickup_address:
            raise serializers.ValidationError({"pickup_address": "Pickup address is required."})
        if not delivery_address:
            raise serializers.ValidationError({"delivery_address": "Delivery address is required."})
        data['pickup_address'] = pickup_address
        data['delivery_address'] = delivery_address

        payment_method = str(data.get('payment_method') or 'CARD').strip().upper()
        if payment_method in {'PAYSTACK', 'CARD'}:
            payment_method = 'CARD'
        elif payment_method in {'CASH', 'CASH_ON_DELIVERY'}:
            payment_method = 'CASH'
        elif payment_method in {'BANK_TRANSFER', 'TRANSFER'}:
            payment_method = 'BANK_TRANSFER'
        else:
            raise serializers.ValidationError({
                "payment_method": "Unsupported payment method. Use CARD, CASH, or BANK_TRANSFER."
            })
        data['payment_method'] = payment_method

        items = data.get('items') or []
        if laundry and items:
            # pyre-ignore[missing-module]
            from laundries.models.service import LaundryService
            offered_pairs = set(
                LaundryService.objects.filter(
                    laundry=laundry,
                    is_available=True,
                    item_id__in=[item_data['item'].id for item_data in items],
                    service_type_id__in=[item_data['service_type'].id for item_data in items],
                ).values_list('item_id', 'service_type_id')
            )
            item_errors = {}
            for index, item_data in enumerate(items):
                item = item_data['item']
                service_type = item_data['service_type']
                if (item.id, service_type.id) not in offered_pairs:
                    item_errors[str(index)] = (
                        f"{laundry.name} does not offer {service_type.name} for {item.name}."
                    )
            if item_errors:
                raise serializers.ValidationError({"items": item_errors})

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
        with transaction.atomic():
            items_data = validated_data.pop('items')
            coupon_obj = validated_data.pop('coupon_obj', None)
            # Discard frontend-only fields not stored on Order model
            validated_data.pop('payment_method', None)
            validated_data.pop('coupon_code', None)
            user = self.context['request'].user

            order = Order.objects.create(
                user=user,
                total_amount=0,
                coupon=coupon_obj,
                **validated_data
            )

            # pyre-ignore[missing-module]
            from laundries.models.service import LaundryService

            for item_data in items_data:
                item_instance = item_data['item']
                service_type_instance = item_data['service_type']
                quantity = item_data.get('quantity', 1)

                try:
                    l_service = LaundryService.objects.get(
                        laundry=order.laundry,
                        item=item_instance,
                        service_type=service_type_instance,
                        is_available=True,
                    )
                except LaundryService.DoesNotExist as exc:
                    raise serializers.ValidationError({
                        "items": f"{order.laundry.name} does not offer {service_type_instance.name} for {item_instance.name}."
                    }) from exc
                item_price = l_service.price

                OrderItem.objects.create(
                    order=order,
                    item=item_instance,
                    service_type=service_type_instance,
                    name=item_instance.name,
                    quantity=quantity,
                    price=item_price
                )

            # pyre-ignore[missing-module]
            from ..services.finance_service import FinanceService
            price_breakdown = FinanceService.calculate_price_breakdown(order, coupon=coupon_obj)
            order.total_amount = Decimal(price_breakdown['total'])
            order.save(update_fields=['total_amount', 'updated_at'])

            if coupon_obj:
                # pyre-ignore[missing-module]
                from ..models.coupons import CouponUsage
                CouponUsage.objects.create(user=user, coupon=coupon_obj, order=order)
                coupon_obj.current_usage += 1
                coupon_obj.save(update_fields=['current_usage'])

            return order
