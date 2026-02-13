from rest_framework import viewsets, status, decorators, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from ..models.base import Order, OrderStatusHistory
from ..services.order_state_machine import OrderStateMachine
from ..serializers.lifecycle import OrderStatusHistorySerializer, OrderTransitionSerializer
from ..permissions import IsOrderParticipant, CanManageLifecycle
import logging

logger = logging.getLogger(__name__)

class OrderLifecycleViewSet(viewsets.GenericViewSet):
    """
    ViewSet for managing order lifecycle transitions and history.
    Enforces strict state machine rules and role-based permissions.
    """
    queryset = Order.objects.all()
    serializer_class = OrderTransitionSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrderParticipant, CanManageLifecycle]

    def get_order(self):
        return get_object_or_404(Order, id=self.kwargs['pk'])

    def _handle_transition(self, request, to_status):
        order = self.get_order()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        reason = serializer.validated_data.get('reason')
        metadata = serializer.validated_data.get('metadata', {})
        
        # Perform atomic transition via State Machine
        updated_order, success = OrderStateMachine.transition(
            order_id=order.id,
            to_status=to_status,
            user=request.user,
            metadata=metadata,
            reason=reason
        )
        
        if not success:
            return Response({
                "status": "error",
                "message": "Invalid state transition",
                "data": {
                    "current_status": order.status,
                    "target_status": to_status
                }
            }, status=status.HTTP_400_BAD_REQUEST)
            
        logger.info(f"Order {order.order_no} transitioned to {to_status} by {request.user.email}")
        
        return Response({
            "status": "success",
            "message": f"Order marked as {to_status}",
            "data": {
                "id": updated_order.id,
                "status": updated_order.status
            }
        })

    @decorators.action(detail=True, methods=['patch'])
    def accept(self, request, pk=None):
        """PENDING -> CONFIRMED (Laundry Only)"""
        return self._handle_transition(request, Order.Status.CONFIRMED)

    @decorators.action(detail=True, methods=['patch'])
    def reject(self, request, pk=None):
        """PENDING -> REJECTED (Laundry Only)"""
        return self._handle_transition(request, Order.Status.REJECTED)

    @decorators.action(detail=True, methods=['patch'], url_path='mark-picked-up')
    def mark_picked_up(self, request, pk=None):
        """CONFIRMED -> PICKED_UP (Rider/Laundry)"""
        return self._handle_transition(request, Order.Status.PICKED_UP)

    @decorators.action(detail=True, methods=['patch'], url_path='mark-washed')
    def mark_washed(self, request, pk=None):
        """PICKED_UP -> IN_PROCESS (Laundry)"""
        return self._handle_transition(request, Order.Status.IN_PROCESS)

    @decorators.action(detail=True, methods=['patch'], url_path='mark-out-for-delivery')
    def mark_out_for_delivery(self, request, pk=None):
        """IN_PROCESS -> OUT_FOR_DELIVERY (Rider/Laundry)"""
        return self._handle_transition(request, Order.Status.OUT_FOR_DELIVERY)

    @decorators.action(detail=True, methods=['patch'], url_path='mark-delivered')
    def mark_delivered(self, request, pk=None):
        """OUT_FOR_DELIVERY -> DELIVERED (Rider/Laundry)"""
        return self._handle_transition(request, Order.Status.DELIVERED)

    @decorators.action(detail=True, methods=['patch'])
    def complete(self, request, pk=None):
        """DELIVERED -> COMPLETED (Laundry)"""
        return self._handle_transition(request, Order.Status.COMPLETED)

    @decorators.action(detail=True, methods=['patch'])
    def cancel(self, request, pk=None):
        """PENDING/CONFIRMED -> CANCELLED (Customer/Laundry)"""
        return self._handle_transition(request, Order.Status.CANCELLED)

    @decorators.action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        """Fetch full audit trail for the order."""
        order = self.get_order()
        history = order.status_history.all()
        serializer = OrderStatusHistorySerializer(history, many=True)
        
        return Response({
            "status": "success",
            "message": "Order timeline fetched",
            "data": serializer.data
        })
        
