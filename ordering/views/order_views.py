# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.decorators import action
from ordering.models import LaunderableItem, BookingSlot, Order
from ordering.serializers import (
    LaunderableItemSerializer, 
    BookingSlotSerializer, 
    OrderDetailSerializer, 
    OrderCreateSerializer
)
from ..services.payment_service import PaymentService

class CatalogViewSet(viewsets.ReadOnlyModelViewSet):
    """Viewset for global catalog of items and services."""
    queryset = LaunderableItem.objects.all()
    serializer_class = LaunderableItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'])
    def items(self, request):
        items = LaunderableItem.objects.filter(is_active=True)
        serializer = self.get_serializer(items, many=True)
        return Response(serializer.data)

class BookingViewSet(viewsets.GenericViewSet):
    """Endpoints for booking, scheduling, and creation."""
    permission_classes = [permissions.IsAuthenticated]

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
        
        total = 0
        for entry in items_inputs:
            item_id = str(entry.get('item'))
            if item_id in items_objs:
                total += float(items_objs[item_id].base_price) * int(entry.get('quantity', 1))
        
        return Response({"estimated_total": total})

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

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).prefetch_related('items')

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return OrderDetailSerializer
    @action(detail=True, methods=['get'], url_path='price-breakdown')
    def price_breakdown(self, request, pk=None):
        """
        Backend-owned financial truth for an order.
        Calculates stored totals and applies business logic using Decimal.
        """
        from decimal import Decimal
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
             return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        items_total = Decimal(str(order.total_amount)) # Simplification: assume total_amount already includes items
        
        # Real logic would sum items
        # items_total = order.items.aggregate(total=Sum(F('quantity') * F('service__base_price')))['total'] or Decimal('0.00')

        delivery_fee = order.laundry.delivery_fee
        discount = Decimal('0.00') # Pull from used coupon if exists
        
        tax_rate = Decimal('0.05') # 5% tax
        tax = items_total * tax_rate
        
        platform_fee = Decimal('0.00')
        
        total = items_total + delivery_fee + tax - discount
        
        breakdown = {
            "items_total": str(items_total),
            "delivery_fee": str(delivery_fee),
            "discount": str(discount),
            "tax": str(tax),
            "platform_fee": str(platform_fee),
            "total": str(total),
            "currency": "NGN"
        }

        # If mismatch with stored total, log it (Financial Integrity Check)
        if total != Decimal(str(order.total_amount)):
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Financial Mismatch on Order {order.id}: Calculated {total} vs Stored {order.total_amount}")

        cache.set(cache_key, breakdown, 300)

        return Response({
            "status": "success",
            "message": "Price breakdown fetched",
            "data": breakdown
        })
