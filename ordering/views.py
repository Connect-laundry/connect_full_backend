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
from .services.payment_service import PaymentService

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
        return OrderCreateSerializer
