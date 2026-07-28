# pyre-ignore[missing-module]
from rest_framework import serializers
from decimal import Decimal
# pyre-ignore[missing-module]
from django.db import transaction
from django.db.models import F
# pyre-ignore[missing-module]
from ordering.models import LaunderableItem, BookingSlot, Order, OrderItem
from laundries.models.category import Category
from laundries.models.laundry import Laundry
# pyre-ignore[missing-module]
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from utils.media import SafeMediaModelSerializer

class LaunderableItemSerializer(SafeMediaModelSerializer):
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
    item = serializers.CharField(max_length=255)
    service_type = serializers.CharField(max_length=255)
    quantity = serializers.IntegerField(min_value=1, max_value=99)

class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    laundryName = serializers.CharField(source='laundry.name', read_only=True)
    price_breakdown = serializers.SerializerMethodField()
    payment_reference = serializers.CharField(source='payment.transaction_reference', read_only=True, default='')
    
    class Meta:
        model = Order
        fields = [
            'id', 'order_no', 'laundryName', 'laundry', 
            'status', 'payment_status', 'total_amount', 
            'price_breakdown',
            'pickup_date', 'delivery_date', 
            'pickup_address', 'pickup_lat', 'pickup_lng',
            'delivery_address', 'delivery_lat', 'delivery_lng',
            'address', 
            'special_instructions', 'items', 'created_at',
            'payment_reference'
        ]

    @extend_schema_field(OpenApiTypes.OBJECT)
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

        if laundry and getattr(laundry, 'vacation_mode', False):
            raise serializers.ValidationError({"laundry": "This laundry is temporarily on vacation and not accepting orders."})

        # Geofencing validation check
        pickup_lat = data.get('pickup_lat')
        pickup_lng = data.get('pickup_lng')
        if laundry and pickup_lat is not None and pickup_lng is not None:
            lat = float(pickup_lat)
            lng = float(pickup_lng)
            
            from ..services.finance_service import FinanceService
            if getattr(laundry, 'service_area_polygon', None):
                inside = FinanceService.is_point_in_polygon(lng, lat, laundry.service_area_polygon)
                if not inside:
                    raise serializers.ValidationError({
                        "pickup_address": "Your pickup address is outside this laundry's service coverage area."
                    })
            elif laundry.latitude is not None and laundry.longitude is not None:
                distance = FinanceService.calculate_haversine_distance(
                    lat, lng, laundry.latitude, laundry.longitude
                )
                if distance is not None and distance > float(laundry.service_radius_km):
                    raise serializers.ValidationError({
                        "pickup_address": f"Your pickup address is {distance:.2f} km away, which is outside the laundry's {laundry.service_radius_km} km service radius."
                    })

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
            from laundries.models.service import LaundryService
            from laundries.models.pricing import LaundryPricingItem

            item_errors = {}
            for index, item_data in enumerate(items):
                item_val = item_data['item']
                service_type_val = item_data['service_type']

                item_id = str(getattr(item_val, 'id', item_val))
                service_type_id = str(getattr(service_type_val, 'id', service_type_val))

                in_service = LaundryService.objects.filter(
                    laundry=laundry,
                    is_available=True,
                    item_id=item_id,
                    service_type_id=service_type_id
                ).exists()

                in_pricing = LaundryPricingItem.objects.filter(
                    laundry=laundry,
                    id=item_id,
                    is_active=True
                ).exists()

                if not in_service and not in_pricing:
                    item_errors[str(index)] = f"{laundry.name} does not offer pricing for item {item_val}."

            if item_errors:
                raise serializers.ValidationError({"items": item_errors})

        coupon_code = data.get('coupon_code')
        if coupon_code:
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
        with transaction.atomic():
            items_data = validated_data.pop('items')
            coupon_obj = validated_data.pop('coupon_obj', None)
            validated_data.pop('payment_method', None)
            validated_data.pop('coupon_code', None)
            user = self.context['request'].user

            order = Order.objects.create(
                user=user,
                total_amount=0,
                coupon=coupon_obj,
                **validated_data
            )

            from laundries.models.service import LaundryService
            from laundries.models.pricing import LaundryPricingItem
            from ordering.models import LaunderableItem, Category

            for item_data in items_data:
                item_val = item_data['item']
                service_type_val = item_data['service_type']
                quantity = item_data.get('quantity', 1)

                item_id = str(getattr(item_val, 'id', item_val))
                service_type_id = str(getattr(service_type_val, 'id', service_type_val))

                item_price = None
                item_name = None
                db_item = None
                db_service_type = None

                try:
                    l_svc = LaundryService.objects.select_related('item', 'service_type').get(
                        laundry=order.laundry,
                        item_id=item_id,
                        service_type_id=service_type_id,
                        is_available=True,
                    )
                    item_price = l_svc.price
                    item_name = l_svc.item.name
                    db_item = l_svc.item
                    db_service_type = l_svc.service_type
                except Exception:
                    try:
                        p_item = LaundryPricingItem.objects.get(
                            laundry=order.laundry,
                            id=item_id,
                            is_active=True
                        )
                        item_price = p_item.unit_price
                        item_name = p_item.item_name
                        db_service_type = Category.objects.filter(id=service_type_id).first()
                    except LaundryPricingItem.DoesNotExist as exc:
                        raise serializers.ValidationError({
                            "items": f"{order.laundry.name} does not offer item {item_id}."
                        }) from exc

                OrderItem.objects.create(
                    order=order,
                    item=db_item,
                    service_type=db_service_type,
                    name=item_name or "Laundry Item",
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
                from ..models.coupons import Coupon, CouponUsage
                # Lock the coupon row to enforce usage limits atomically and
                # prevent concurrent redemptions from exceeding max_usage.
                locked_coupon = Coupon.objects.select_for_update().get(pk=coupon_obj.pk)
                if (
                    locked_coupon.max_usage is not None
                    and locked_coupon.current_usage >= locked_coupon.max_usage
                ):
                    raise serializers.ValidationError(
                        {"coupon_code": "Coupon has reached its usage limit."}
                    )
                CouponUsage.objects.create(user=user, coupon=locked_coupon, order=order)
                Coupon.objects.filter(pk=locked_coupon.pk).update(
                    current_usage=F('current_usage') + 1
                )

            return order
