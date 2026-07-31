# pyre-ignore[missing-module]
from rest_framework import viewsets, status, decorators, permissions
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.shortcuts import get_object_or_404
# pyre-ignore[missing-module]
from ..models.base import Order, OrderStatusHistory
# pyre-ignore[missing-module]
from ..services.order_state_machine import OrderStateMachine
# pyre-ignore[missing-module]
from ..serializers.lifecycle import OrderStatusHistorySerializer, OrderTransitionSerializer
# pyre-ignore[missing-module]
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
        obj = get_object_or_404(Order, id=self.kwargs['pk'])
        self.check_object_permissions(self.request, obj)
        return obj

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
        """
        OUT_FOR_DELIVERY -> DELIVERED (Laundry)

        Accepts an optional ``handover_code``: the four digits the customer
        reads out when they take their clothes back. A correct code proves the
        delivery and releases the customer's payment immediately.

        A wrong code is rejected outright, because a laundry typing digits at
        random must not be able to stumble into an instant payout. Sending no
        code at all is allowed and still closes the order, since customers are
        not always reachable, but the money then waits out a dispute window
        before it can be paid.
        """
        from ..services.handover import mark_confirmed_by_code, verify_handover_code

        submitted = request.data.get('handover_code')
        order = self.get_order()

        if submitted:
            if not verify_handover_code(order, submitted):
                return Response({
                    "status": "error",
                    "message": "That handover code does not match this order.",
                    "data": {"field": "handover_code"},
                }, status=status.HTTP_400_BAD_REQUEST)
            mark_confirmed_by_code(order)

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
        
