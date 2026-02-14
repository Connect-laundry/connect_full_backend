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
    CouponValidationSerializer
# pyre-ignore[missing-module]
)
# pyre-ignore[missing-module]
from ..services.payment_service import PaymentService
from decimal import Decimal

class CatalogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Viewset for global catalog of items and services.
    Strictly filters for active items from approved/active laundries.
    """
    queryset = LaunderableItem.objects.filter(
        is_active=True,
        laundry__status='APPROVED',
        laundry__is_active=True
    ).select_related('laundry')
    serializer_class = LaunderableItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset()

    @action(detail=False, methods=['get'])
    def items(self, request):
        # Already filtered by queryset, but keeping this for explicit backward compatibility if used
        return self.list(request)

class BookingViewSet(viewsets.GenericViewSet):
    """Endpoints for booking, scheduling, and creation."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'burst_user'

    @action(detail=False, methods=['get'])
    def schedule(self, request):
        laundry_id = request.query_params.get('laundry_id')
        if not laundry_id:
            return Response({"error": "laundry_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        slots = BookingSlot.objects.filter(laundry_id=laundry_id, is_available=True)
        serializer = BookingSlotSerializer(slots, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def estimate(self, request):
        items_inputs = request.data.get('items', [])
        if not items_inputs:
            return Response({"error": "items are required"}, status=status.HTTP_400_BAD_REQUEST)
             
        item_ids = [i.get('item') for i in items_inputs if i.get('item')]
        items_objs = {str(item.id): item for item in LaunderableItem.objects.filter(id__in=item_ids)}
        
        total = Decimal('0.00')
        for entry in items_inputs:
            item_id = str(entry.get('item'))
            if item_id in items_objs:
                total += Decimal(str(items_objs[item_id].base_price)) * Decimal(str(entry.get('quantity', 1)))
        
        return Response({"estimated_total": str(total)})

    @action(detail=False, methods=['post'])
    def calculate(self, request):
        """Alias for estimate to match frontend requirement"""
        return self.estimate(request)

    @action(detail=False, methods=['post'])
    def create(self, request):
        serializer = OrderCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            order = serializer.save()
            
            # Initiate mock payment
            payment_info = PaymentService.create_payment_intent(order)
            
            response_data = OrderDetailSerializer(order).data
            response_data['payment_intent'] = payment_info
            
            return Response(response_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class OrderViewSet(viewsets.ModelViewSet):
    """Viewset for managing and tracking orders."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'burst_user'

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).prefetch_related('items')

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return OrderDetailSerializer
        return OrderCreateSerializer

    @action(detail=True, methods=['get'], url_path='price-breakdown')
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
            return Response({
                "status": "success",
                "message": "Price breakdown fetched (cached)",
                "data": cached_data
            })

        order = self.get_object()
        
        # Security: Only owner or laundry owner
        if order.user != request.user and order.laundry.owner != request.user and not request.user.is_staff:
             return Response({"status": "error", "message": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        # Use centralized finance service
        breakdown = FinanceService.calculate_price_breakdown(order, coupon=order.coupon)

        cache.set(cache_key, breakdown, 300)

        return Response({
            "status": "success",
            "message": "Price breakdown fetched",
            "data": breakdown
        })

class CouponViewSet(viewsets.GenericViewSet):
    """Viewset for validating and listing available coupons."""
    serializer_class = CouponSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return Coupon.objects.filter(is_active=True)

    @action(detail=False, methods=['post'], url_path='validate')
    def validate(self, request):
        serializer = CouponValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        code = serializer.validated_data['code']
        laundry_id = serializer.validated_data['laundry_id']
        items_total = serializer.validated_data['items_total']
        
        try:
            coupon = Coupon.objects.get(code=code, is_active=True)
            is_valid, error = coupon.is_valid(
                user=request.user, 
                laundry_id=laundry_id, 
                order_value=items_total
            )
            
            if not is_valid:
                return Response({
                    "status": "error",
                    "message": error,
                    "valid": False
                }, status=status.HTTP_400_BAD_REQUEST)
                
            discount = Decimal('0.00')
            if coupon.discount_type == 'FIXED':
                discount = Decimal(str(coupon.discount_value))
            else:
                discount = (Decimal(str(items_total)) * (Decimal(str(coupon.discount_value)) / 100))
            
            discount = min(discount, Decimal(str(items_total)))
            
            return Response({
                "status": "success",
                "message": "Coupon is valid",
                "valid": True,
                "data": {
                    "code": coupon.code,
                    "discount_amount": str(discount.quantize(Decimal('0.01'))),
                    "discount_type": coupon.discount_type,
                    "discount_value": str(coupon.discount_value)
                }
            })
            
        except Coupon.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Invalid coupon code.",
                "valid": False
            }, status=status.HTTP_404_NOT_FOUND)
