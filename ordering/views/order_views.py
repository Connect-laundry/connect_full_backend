# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status

# pyre-ignore[missing-module]
from rest_framework.response import Response

# pyre-ignore[missing-module]
from rest_framework.decorators import action

# pyre-ignore[missing-module]
from ordering.models import LaunderableItem, BookingSlot, Order, Coupon
from ordering.serializers import (
    LaunderableItemSerializer,
    BookingSlotSerializer,
    OrderDetailSerializer,
    OrderCreateSerializer,
    CouponSerializer,
    CouponValidationSerializer,
)

# pyre-ignore[missing-module]
from ..services.payment_service import PaymentService
from django.utils import timezone
from decimal import Decimal


class CatalogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Viewset for global catalog of items and services.
    Strictly filters for active items from approved/active laundries.
    """

    queryset = LaunderableItem.objects.filter(is_active=True).prefetch_related(
        "item_category"
    )
    serializer_class = LaunderableItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=["get"])
    def services(self, request):
        """Returns the list of service types (Wash, Iron, etc)"""
        # pyre-ignore[missing-module]
        from laundries.models.category import Category

        # pyre-ignore[missing-module]
        from laundries.serializers.category import CategorySerializer

        services = Category.objects.filter(type="SERVICE_TYPE")
        serializer = CategorySerializer(services, many=True)
        return Response(serializer.data)

    def list(self, request, *args, **kwargs):
        # Alias for backward compatibility if /booking/services/ was pointing
        # to list
        return self.services(request)

    @action(detail=False, methods=["get"])
    def items(self, request):
        """Returns the actual catalog of items with supported services"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class BookingViewSet(viewsets.GenericViewSet):
    """Endpoints for booking, scheduling, and creation."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "burst_user"

    @action(detail=False, methods=["get"])
    def schedule(self, request):
        laundry_id = request.query_params.get("laundry_id")
        if not laundry_id:
            return Response(
                {"error": "laundry_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        slots = BookingSlot.objects.filter(laundry_id=laundry_id, is_available=True)
        serializer = BookingSlotSerializer(slots, many=True)
        return Response(
            {
                "success": True,
                "message": "Available slots fetched successfully.",
                "data": serializer.data,
            }
        )

    @action(detail=False, methods=["post"])
    def estimate(self, request):
        """
        Provides a real-time price breakdown before the user clicks 'Confirm Order'.
        Uses FinanceService and LaundryService for vendor-specific accuracy.
        """
        laundry_id = request.data.get("laundry")
        items_data = request.data.get("items", [])

        if not laundry_id or not items_data:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": "laundry and items are required for estimation.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 1. Create a transient order object (not saved to DB)
        try:
            from laundries.models.laundry import Laundry

            laundry = Laundry.objects.get(id=laundry_id)
        except Laundry.DoesNotExist:
            return Response(
                {"success": False, "status": "error", "message": "Laundry not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 2. Extract specific prices from LaundryService
        from laundries.models.service import LaundryService
        from ordering.models import OrderItem

        # We'll create a mock Order and mock OrderItems in memory
        temp_order = Order(laundry=laundry, user=request.user)

        total_items_price = Decimal("0.00")
        errors = []
        for data in items_data:
            item_id = data.get("item")
            service_type_id = data.get("service_type")
            quantity = data.get("quantity", 1)

            try:
                l_svc = LaundryService.objects.get(
                    laundry_id=laundry_id,
                    item_id=item_id,
                    service_type_id=service_type_id,
                )
                total_items_price += l_svc.price * Decimal(str(quantity))
            except LaundryService.DoesNotExist:
                errors.append(
                    f"Price not found! Ensure 'item' is the 'itemId' (NOT 'id') and 'service_type' "
                    f"is the 'serviceTypeId'. Failed for item: {item_id}, service: {service_type_id}"
                )

        if errors:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": "Invalid item or service type IDs.",
                    "data": {"errors": errors},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Use FinanceService for the full breakdown
        from ..services.finance_service import FinanceService

        # Note: Since calculate_price_breakdown usually queries the DB for items,
        # we might need a modified version or just calculate manually here if
        # it's simpler for preview.

        # Let's do a semi-manual calculation for the preview to avoid DB order
        # creation
        delivery_fee = FinanceService.calculate_delivery_fee(temp_order)
        pickup_fee = FinanceService.calculate_pickup_fee(temp_order)
        # Platform fee & Tax logic
        tax = FinanceService.calculate_tax_amount(total_items_price)
        from django.conf import settings

        platform_fee = (
            total_items_price * Decimal(str(settings.PLATFORM_FEE_RATE))
        ).quantize(Decimal("0.01"))

        total = total_items_price + delivery_fee + pickup_fee + tax + platform_fee

        return Response(
            {
                "items_total": str(total_items_price.quantize(Decimal("0.01"))),
                "delivery_fee": str(delivery_fee.quantize(Decimal("0.01"))),
                "pickup_fee": str(pickup_fee.quantize(Decimal("0.01"))),
                "tax": str(tax.quantize(Decimal("0.01"))),
                "platform_fee": str(platform_fee.quantize(Decimal("0.01"))),
                "total": str(total.quantize(Decimal("0.01"))),
                "currency": getattr(settings, "CURRENCY", "GHS"),
            }
        )

    @action(detail=False, methods=["post"])
    def calculate(self, request):
        """Alias for estimate to match frontend requirement"""
        return self.estimate(request)

    @action(detail=False, methods=["post"])
    def create(self, request):
        serializer = OrderCreateSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            order = serializer.save()

            # Initiate mock payment
            payment_info = PaymentService.create_payment_intent(order)

            response_data = OrderDetailSerializer(order).data
            response_data["payment_intent"] = payment_info

            return Response(response_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OrderViewSet(viewsets.ModelViewSet):
    """Viewset for managing and tracking orders."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "burst_user"

    def get_queryset(self):
        from django.db.models import Q

        user = self.request.user
        if user.is_staff:
            return (
                Order.objects.all()
                .prefetch_related(
                    "items__item", "items__service_type", "status_history"
                )
                .select_related("laundry", "laundry__owner", "coupon")
            )

        return (
            Order.objects.filter(Q(user=user) | Q(laundry__owner=user))
            .prefetch_related("items__item", "items__service_type", "status_history")
            .select_related("laundry", "laundry__owner", "coupon")
        )

    def get_serializer_class(self):
        if self.action in ["list", "retrieve", "active"]:
            return OrderDetailSerializer
        return OrderCreateSerializer

    @action(detail=True, methods=["get"], url_path="price-breakdown")
    def price_breakdown(self, request, pk=None):
        """
        Calculates stored totals and applies business logic using FinanceService.
        """
        # pyre-ignore[missing-module]
        from ..services.finance_service import FinanceService

        # pyre-ignore[missing-module]
        from django.core.cache import cache

        cache_key = f"order_breakdown_{pk}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(
                {
                    "success": True,
                    "message": "Price breakdown fetched (cached)",
                    "data": cached_data,
                }
            )

        order = self.get_object()

        # Security: Only owner or laundry owner
        if (
            order.user != request.user
            and order.laundry.owner != request.user
            and not request.user.is_staff
        ):
            return Response(
                {"success": False, "status": "error", "message": "Unauthorized"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Use centralized finance service
        breakdown = FinanceService.calculate_price_breakdown(order, coupon=order.coupon)

        cache.set(cache_key, breakdown, 300)

        return Response(
            {"success": True, "message": "Price breakdown fetched", "data": breakdown}
        )

    @action(detail=False, methods=["get"])
    def active(self, request):
        """Returns a list of active (in-progress) orders for the current user."""
        active_statuses = [
            Order.Status.PENDING,
            Order.Status.CONFIRMED,
            Order.Status.PICKED_UP,
            Order.Status.IN_PROCESS,
            Order.Status.OUT_FOR_DELIVERY,
        ]
        queryset = self.get_queryset().filter(status__in=active_statuses)
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {"success": True, "count": len(serializer.data), "data": serializer.data}
        )

    @action(detail=True, methods=["patch"], url_path="update-weight")
    def update_weight(self, request, pk=None):
        """
        Action for laundry staff to record the actual weight of a Per Kg order.
        Recalculates the total amount and transitions status.
        """
        order = self.get_object()

        # 1. Authorization: Only Laundry Owner or Staff
        if order.laundry.owner != request.user and not request.user.is_staff:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": "You do not have permission to update weight for this order.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if order.pricing_method != "PER_KG":
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": "This order is not a weight-based order.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        actual_weight = request.data.get("actual_weight")
        if not actual_weight:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": "actual_weight is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order.actual_weight = Decimal(str(actual_weight))

            # Recalculate Final Price
            from ..services.finance_service import FinanceService

            breakdown = FinanceService.calculate_price_breakdown(
                order, coupon=order.coupon
            )
            order.final_price = Decimal(breakdown["total"])

            # Transition to WEIGHED status
            previous_status = order.status
            order.status = Order.Status.WEIGHED
            order.save()

            # Create History Record
            from ordering.models import OrderStatusHistory

            OrderStatusHistory.objects.create(
                order=order,
                previous_status=previous_status,
                new_status=order.status,
                changed_by=request.user,
                metadata={
                    "action": "update_weight",
                    "actual_weight": str(actual_weight),
                },
            )

            return Response(
                {
                    "success": True,
                    "message": "Order weighed. Waiting for user to confirm final price.",
                    "data": OrderDetailSerializer(order).data,
                }
            )

        except (ValueError, TypeError, Exception) as e:
            return Response(
                {"success": False, "status": "error", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class CouponViewSet(viewsets.GenericViewSet):
    """Viewset for validating and listing available coupons."""

    serializer_class = CouponSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Coupon.objects.filter(is_active=True)

    @action(detail=False, methods=["post"], url_path="validate")
    def validate(self, request):
        serializer = CouponValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        code = serializer.validated_data["code"]
        laundry_id = serializer.validated_data["laundry_id"]
        items_total = serializer.validated_data["items_total"]

        try:
            coupon = Coupon.objects.get(code=code, is_active=True)
            is_valid, error = coupon.is_valid(
                user=request.user, laundry_id=laundry_id, order_value=items_total
            )

            if not is_valid:
                return Response(
                    {"success": False, "message": error, "valid": False},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            discount = Decimal("0.00")
            if coupon.discount_type == "FIXED":
                discount = Decimal(str(coupon.discount_value))
            else:
                discount = Decimal(str(items_total)) * (
                    Decimal(str(coupon.discount_value)) / 100
                )

            discount = min(discount, Decimal(str(items_total)))

            return Response(
                {
                    "success": True,
                    "message": "Coupon is valid",
                    "valid": True,
                    "data": {
                        "code": coupon.code,
                        "discount_amount": str(discount.quantize(Decimal("0.01"))),
                        "discount_type": coupon.discount_type,
                        "discount_value": str(coupon.discount_value),
                    },
                }
            )

        except Coupon.DoesNotExist:
            return Response(
                {"success": False, "message": "Invalid coupon code.", "valid": False},
                status=status.HTTP_404_NOT_FOUND,
            )
