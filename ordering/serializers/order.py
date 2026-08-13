import uuid as uuid_lib
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

def _as_uuid(value):
    """Coerce a booking line-item reference to a UUID, or None when it isn't one.

    ``LaundryService`` rows are keyed by UUIDs (``LaunderableItem`` /
    ``Category``), but a laundry on the owner-defined ``LaundryPricingItem``
    catalog has no ``Category`` row at all — its "service type" is the free-form
    ``category`` label ("Wash Only", "Wash & Iron"). Feeding that label straight
    into a UUID column raises ``ValidationError: "Wash Only" is not a valid
    UUID`` and fails the whole booking, so callers must skip UUID-keyed lookups
    when this returns None.
    """
    if value in (None, ''):
        return None
    try:
        return uuid_lib.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


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
    payment_reference = serializers.SerializerMethodField()
    provider_payment_status = serializers.SerializerMethodField()
    payment_state = serializers.SerializerMethodField()
    amount_due = serializers.SerializerMethodField()
    amount_collected = serializers.SerializerMethodField()
    cash_collected_at = serializers.SerializerMethodField()
    
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
            'payment_reference', 'provider_payment_status', 'payment_method',
            'payment_state', 'amount_due', 'amount_collected', 'cash_collected_at',
            # Lets the app and owner tell a quote request or a weight order
            # apart from an itemised one on the tracking and receipt screens.
            'pricing_mode', 'estimated_weight_kg',
        ]

    @staticmethod
    def _payment(obj):
        try:
            return obj.payment
        except Exception:
            return None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_payment_reference(self, obj):
        if obj.payment_method == Order.PaymentMethod.CASH:
            return None
        payment = self._payment(obj)
        return payment.transaction_reference if payment else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_provider_payment_status(self, obj):
        if obj.payment_method == Order.PaymentMethod.CASH:
            return None
        payment = self._payment(obj)
        return payment.status if payment else None

    @extend_schema_field(OpenApiTypes.STR)
    def get_payment_state(self, obj):
        if obj.pricing_mode == Order.PricingMode.CUSTOM_QUOTE and obj.priced_at is None:
            return 'AWAITING_QUOTE'
        if obj.payment_method == Order.PaymentMethod.CASH:
            return 'CASH_COLLECTED' if obj.payment_status == Order.PaymentStatus.PAID else 'CASH_DUE'
        if obj.payment_status == Order.PaymentStatus.PAID:
            return 'PAID'
        payment = self._payment(obj)
        return f'ONLINE_{payment.status}' if payment else 'ONLINE_REQUIRED'

    @extend_schema_field(OpenApiTypes.STR)
    def get_amount_due(self, obj):
        return str(Decimal('0.00') if obj.payment_status == Order.PaymentStatus.PAID else obj.total_amount)

    @extend_schema_field(OpenApiTypes.STR)
    def get_amount_collected(self, obj):
        payment = self._payment(obj)
        if (
            payment
            and obj.payment_method == Order.PaymentMethod.CASH
            and obj.payment_status == Order.PaymentStatus.PAID
        ):
            return str(payment.amount_collected)
        return '0.00'

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_cash_collected_at(self, obj):
        payment = self._payment(obj)
        if payment and obj.payment_method == Order.PaymentMethod.CASH:
            return payment.paid_at
        return None

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_price_breakdown(self, obj):
        from ..services.finance_service import FinanceService

        return FinanceService.calculate_price_breakdown(obj, coupon=obj.coupon)

class OrderCreateSerializer(serializers.ModelSerializer):
    # Empty is allowed at the field level because by-weight and quote orders
    # carry no items. Whether items are required is decided per pricing mode in
    # validate(), so an itemised order with no items is still rejected.
    items = OrderItemCreateSerializer(many=True, required=False, allow_empty=True, default=list)
    laundry = serializers.PrimaryKeyRelatedField(queryset=Laundry.objects.all())
    coupon_code = serializers.CharField(required=False, write_only=True)
    pricing_mode = serializers.ChoiceField(
        choices=Order.PricingMode.choices, required=False, default=Order.PricingMode.BY_ITEM
    )
    estimated_weight_kg = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )

    # Accept GPS coords and payment_method from frontend
    pickup_lat = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    pickup_lng = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    delivery_lat = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    delivery_lng = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    payment_method = serializers.ChoiceField(
        choices=['CARD', 'PAYSTACK', 'CASH', 'CASH_ON_DELIVERY', 'BANK_TRANSFER', 'TRANSFER'],
        required=False,
        default=Order.PaymentMethod.CARD,
    )

    class Meta:
        model = Order
        fields = [
            'laundry', 'pickup_date', 
            'pickup_address', 'pickup_lat', 'pickup_lng',
            'delivery_address', 'delivery_lat', 'delivery_lng',
            'special_instructions', 'items', 'coupon_code',
            'payment_method', 'pricing_mode', 'estimated_weight_kg',
        ]

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = data.copy()
            if 'address' in data and not data.get('pickup_address'):
                data['pickup_address'] = data['address']
            if 'address' in data and not data.get('delivery_address'):
                data['delivery_address'] = data['address']
            harmless_aliases = {'address', 'idempotency_key', 'pickup_time', 'delivery_time'}
            unsupported_fields = sorted(set(data) - set(self.fields) - harmless_aliases)
            if unsupported_fields:
                raise serializers.ValidationError({
                    "non_field_errors": [
                        f"Unsupported booking fields: {', '.join(unsupported_fields)}."
                    ]
                })
            # Clean copy without harmless aliases before validation
            data = {k: v for k, v in data.items() if k in set(self.fields)}
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

        pricing_mode = data.get('pricing_mode') or Order.PricingMode.BY_ITEM
        items = data.get('items') or []
        weight = data.get('estimated_weight_kg')

        # The payment method is an order-level business choice. It remains
        # explicit even while a custom quote is waiting for its final price.
        payment_method = str(data.get('payment_method') or Order.PaymentMethod.CARD).upper()
        if payment_method in {'PAYSTACK', 'CARD'}:
            data['payment_method'] = Order.PaymentMethod.CARD
        elif payment_method in {'CASH', 'CASH_ON_DELIVERY'}:
            data['payment_method'] = Order.PaymentMethod.CASH
        else:
            data['payment_method'] = Order.PaymentMethod.BANK_TRANSFER

        # Per-mode requirements. Each mode carries exactly what it needs and
        # nothing it does not, so a stray weight on an itemised order or missing
        # items on one is caught here rather than producing a malformed order.
        if pricing_mode == Order.PricingMode.BY_ITEM:
            if not items:
                raise serializers.ValidationError({"items": "At least one item is required."})
        elif pricing_mode == Order.PricingMode.BY_WEIGHT:
            if weight is None or Decimal(str(weight)) <= 0:
                raise serializers.ValidationError(
                    {"estimated_weight_kg": "An estimated weight is required for a by-weight order."}
                )
            weight_pricing = getattr(laundry, 'weight_pricing', None) if laundry else None
            if weight_pricing is None or not getattr(weight_pricing, 'is_active', False):
                raise serializers.ValidationError(
                    {"pricing_mode": "This laundry does not offer weight-based pricing."}
                )
        elif pricing_mode == Order.PricingMode.CUSTOM_QUOTE:
            # A quote request is just that: no items and no price yet.
            data['items'] = []
            items = []

        if laundry and items:
            from laundries.models.service import LaundryService
            from laundries.models.pricing import LaundryPricingItem

            item_errors = {}
            for index, item_data in enumerate(items):
                item_val = item_data['item']
                service_type_val = item_data['service_type']

                item_uuid = _as_uuid(getattr(item_val, 'id', item_val))
                service_type_uuid = _as_uuid(getattr(service_type_val, 'id', service_type_val))

                in_service = bool(item_uuid and service_type_uuid) and (
                    LaundryService.objects.filter(
                        laundry=laundry,
                        is_available=True,
                        item_id=item_uuid,
                        service_type_id=service_type_uuid
                    ).exists()
                )

                in_pricing = bool(item_uuid) and (
                    LaundryPricingItem.objects.filter(
                        laundry=laundry,
                        id=item_uuid,
                        is_active=True
                    ).exists()
                )

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
            items_data = validated_data.pop('items', []) or []
            coupon_obj = validated_data.pop('coupon_obj', None)
            validated_data.pop('coupon_code', None)
            pricing_mode = validated_data.get('pricing_mode') or Order.PricingMode.BY_ITEM
            user = self.context['request'].user

            order = Order.objects.create(
                user=user,
                total_amount=0,
                coupon=coupon_obj,
                **validated_data
            )

            # By weight: the price comes from the laundry's tariff, computed
            # server-side so the client's estimate is never trusted. A single
            # line item stands in for the weigh-in, so receipts and settlement
            # have a row to work from just like an itemised order.
            if pricing_mode == Order.PricingMode.BY_WEIGHT:
                from ..services.finance_service import FinanceService
                weight_pricing = getattr(order.laundry, 'weight_pricing', None)
                try:
                    price = FinanceService.compute_weight_price(
                        weight_pricing, order.estimated_weight_kg
                    )
                except ValueError as exc:
                    raise serializers.ValidationError({"estimated_weight_kg": str(exc)})

                OrderItem.objects.create(
                    order=order,
                    item=None,
                    service_type=None,
                    name=f"Laundry by weight ({order.estimated_weight_kg} kg est.)",
                    quantity=1,
                    price=price,
                )
                price_breakdown = FinanceService.freeze_price_breakdown(order, coupon=coupon_obj)
                return order

            # Pay after quote: nothing is priced yet. The order is left as a
            # pending request with no items and no frozen price; the laundry
            # quotes it later and the customer pays that invoice in-app. It is
            # deliberately not frozen, so the later quote is what the customer
            # sees rather than a zero.
            if pricing_mode == Order.PricingMode.CUSTOM_QUOTE:
                return order

            from laundries.models.service import LaundryService
            from laundries.models.pricing import LaundryPricingItem
            from laundries.models.category import Category
            from ordering.models import LaunderableItem

            for item_data in items_data:
                item_val = item_data['item']
                service_type_val = item_data['service_type']
                quantity = item_data.get('quantity', 1)

                item_uuid = _as_uuid(getattr(item_val, 'id', item_val))
                service_type_uuid = _as_uuid(getattr(service_type_val, 'id', service_type_val))

                item_price = None
                item_name = None
                db_item = None
                db_service_type = None

                l_svc = None
                if item_uuid and service_type_uuid:
                    l_svc = LaundryService.objects.select_related('item', 'service_type').filter(
                        laundry=order.laundry,
                        item_id=item_uuid,
                        service_type_id=service_type_uuid,
                        is_available=True,
                    ).first()

                if l_svc is not None:
                    item_price = l_svc.price
                    item_name = l_svc.item.name
                    db_item = l_svc.item
                    db_service_type = l_svc.service_type
                else:
                    p_item = (
                        LaundryPricingItem.objects.filter(
                            laundry=order.laundry,
                            id=item_uuid,
                            is_active=True
                        ).first()
                        if item_uuid
                        else None
                    )
                    if p_item is None:
                        raise serializers.ValidationError({
                            "items": f"{order.laundry.name} does not offer item {item_val}."
                        })
                    item_price = p_item.unit_price
                    item_name = p_item.item_name
                    # Owner-defined catalogs have no Category row; the service
                    # label is already captured in the item name snapshot.
                    db_service_type = (
                        Category.objects.filter(id=service_type_uuid).first()
                        if service_type_uuid
                        else None
                    )

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
            # Freeze the pricing onto the order. Every later read, and the
            # settlement owed to the laundry, uses these stored numbers.
            price_breakdown = FinanceService.freeze_price_breakdown(order, coupon=coupon_obj)

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
