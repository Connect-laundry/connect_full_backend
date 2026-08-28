# pyre-ignore[missing-module]
from rest_framework import viewsets, status, decorators, permissions, serializers
# pyre-ignore[missing-module]
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
# pyre-ignore[missing-module]
from django.shortcuts import get_object_or_404
from django.db import transaction
# pyre-ignore[missing-module]
from ..models.base import Order, OrderStatusHistory
# pyre-ignore[missing-module]
from ..services.order_state_machine import OrderStateMachine
# pyre-ignore[missing-module]
from ..serializers.lifecycle import OrderStatusHistorySerializer, OrderTransitionSerializer
from ..serializers.order import OrderDetailSerializer
# pyre-ignore[missing-module]
from ..permissions import IsOrderParticipant, CanManageLifecycle
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


class CashCollectionSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

class CashCollectionResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    already_collected = serializers.BooleanField()
    data = OrderDetailSerializer()

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
        
        from payments.models import Payment

        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order.id)
            payment = (
                Payment.objects.select_for_update()
                .filter(order_id=order.id)
                .first()
            )

            if (
                to_status == Order.Status.COMPLETED
                and order.payment_method == Order.PaymentMethod.CASH
                and order.payment_status != Order.PaymentStatus.PAID
            ):
                return Response({
                    'status': 'error',
                    'message': 'Confirm cash collection before completing this COD order.',
                }, status=status.HTTP_409_CONFLICT)

            if to_status == Order.Status.CONFIRMED:
                accepts_before_payment = (
                    order.payment_method == Order.PaymentMethod.CASH
                    or order.pricing_mode == Order.PricingMode.CUSTOM_QUOTE
                )
                if (
                    not accepts_before_payment
                    and (payment is None or payment.status != Payment.Status.SUCCESS)
                ):
                    return Response({
                        "status": "error",
                        "message": "Payment must be confirmed before this order can be accepted.",
                    }, status=status.HTTP_409_CONFLICT)

            if payment and payment.payment_method != Payment.Method.CASH:
                unsettled_or_paid = {
                    Payment.Status.PENDING,
                    Payment.Status.SUCCESS,
                    Payment.Status.REFUND_PENDING,
                }
                if (
                    to_status in {Order.Status.CANCELLED, Order.Status.REJECTED}
                    and payment.status in unsettled_or_paid
                ):
                    return Response({
                        "status": "error",
                        "message": "Complete payment verification or refund before cancelling this order.",
                    }, status=status.HTTP_409_CONFLICT)
            # The state machine reuses the surrounding transaction and locks
            # the order before validating the transition.
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

    @extend_schema(
        request=CashCollectionSerializer,
        responses=CashCollectionResponseSerializer,
    )
    @decorators.action(
        detail=True,
        methods=['post'],
        url_path='collect-cash',
        serializer_class=CashCollectionSerializer,
    )
    def collect_cash(self, request, pk=None):
        """Confirm COD cash received at fulfillment without touching Paystack."""
        from payments.models import Payment
        from django.utils import timezone
        from marketplace.services.audit import record_audit
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification

        order = self.get_order()
        if not (
            request.user.is_staff
            or request.user.role == 'ADMIN'
            or order.laundry.owner_id == request.user.id
        ):
            return Response(
                {"status": "error", "message": "Only the laundry can confirm cash collection."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submitted_amount = serializer.validated_data['amount']
        allowed_statuses = {
            Order.Status.OUT_FOR_DELIVERY,
            Order.Status.DELIVERED,
            Order.Status.COMPLETED,
        }

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            if order.payment_method != Order.PaymentMethod.CASH:
                return Response(
                    {"status": "error", "message": "This order is not cash on delivery."},
                    status=status.HTTP_409_CONFLICT,
                )
            if order.status not in allowed_statuses:
                return Response(
                    {
                        "status": "error",
                        "message": "Cash can be confirmed only at or after delivery.",
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if submitted_amount != order.total_amount:
                return Response(
                    {
                        "status": "error",
                        "message": f"Collected amount must equal GHS {order.total_amount}.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payment = Payment.objects.select_for_update().filter(order=order).first()
            if order.payment_status == Order.PaymentStatus.PAID:
                if (
                    payment
                    and payment.payment_method == Payment.Method.CASH
                    and payment.status == Payment.Status.SUCCESS
                    and payment.amount_collected == submitted_amount
                ):
                    return Response({
                        "status": "success",
                        "message": "Cash collection was already confirmed.",
                        "already_collected": True,
                        "data": OrderDetailSerializer(order).data,
                    })
                return Response(
                    {"status": "error", "message": "This order is already marked paid."},
                    status=status.HTTP_409_CONFLICT,
                )

            collected_at = timezone.now()
            if payment:
                if payment.payment_method != Payment.Method.CASH:
                    return Response(
                        {"status": "error", "message": "An online payment already exists for this order."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if payment.status != Payment.Status.PENDING:
                    return Response(
                        {"status": "error", "message": "The existing cash payment cannot be collected."},
                        status=status.HTTP_409_CONFLICT,
                    )
                payment.amount = order.total_amount
                payment.amount_collected = submitted_amount
                payment.status = Payment.Status.SUCCESS
                payment.transaction_reference = None
                payment.paystack_reference = None
                payment.paid_at = collected_at
                payment.collected_by = request.user
                payment.save(update_fields=[
                    'amount', 'amount_collected', 'status', 'transaction_reference',
                    'paystack_reference', 'paid_at', 'collected_by', 'updated_at',
                ])
            else:
                payment = Payment.objects.create(
                    user=order.user,
                    order=order,
                    amount=order.total_amount,
                    amount_collected=submitted_amount,
                    currency=order.currency,
                    payment_method=Payment.Method.CASH,
                    status=Payment.Status.SUCCESS,
                    transaction_reference=None,
                    paystack_reference=None,
                    paid_at=collected_at,
                    collected_by=request.user,
                    raw_response={"source": "owner_cash_collection"},
                )

            order.payment_status = Order.PaymentStatus.PAID
            order.save(update_fields=['payment_status', 'updated_at'])
            record_audit(
                action="CASH_COLLECTED",
                actor=request.user,
                request=request,
                target_type="Payment",
                target_id=str(payment.id),
                target_repr=f"Cash collected for {order.order_no}",
                metadata={
                    "order_id": str(order.id),
                    "amount": str(submitted_amount),
                    "currency": order.currency,
                },
            )

        NotificationService.notify_user(
            user=order.user,
            title="Cash payment confirmed",
            body=f"Your cash payment of GHS {submitted_amount} for order {order.order_no} was confirmed.",
            type=Notification.Type.ORDER,
            category="CASH_COLLECTED",
            related_order=order,
            dedup_key=f"payment_success_user:{payment.id}",
        )

        return Response({
            "status": "success",
            "message": "Cash collection confirmed.",
            "already_collected": False,
            "data": OrderDetailSerializer(order).data,
        })

    @decorators.action(detail=True, methods=['post'])
    def quote(self, request, pk=None):
        """
        Price a pay-after-quote order (Laundry only).

        This is the other half of the Pay After flow: the customer requested a
        pickup with no price, the laundry weighed and inspected the items, and
        now sends the invoice. Adding the priced lines here freezes the total
        and makes the order payable, so the customer's payment screen can charge
        it through the normal path.

        Body: ``{"items": [{"name": str, "quantity": int, "price": "0.00"}]}``.
        """
        from decimal import Decimal, InvalidOperation
        from django.db import transaction
        from ..models.base import OrderItem
        from ..services.finance_service import FinanceService
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification

        order = self.get_order()

        # Owner-only, and only for a quote request that has not been priced yet.
        if not (request.user.is_staff or order.laundry.owner_id == request.user.id):
            return Response(
                {"status": "error", "message": "Only the laundry can quote this order."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if order.pricing_mode != Order.PricingMode.CUSTOM_QUOTE:
            return Response(
                {"status": "error", "message": "This order is not a quote request."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if order.items.exists() or order.payment_status == Order.PaymentStatus.PAID:
            return Response(
                {"status": "error", "message": "This order has already been quoted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_items = request.data.get('items')
        if not isinstance(raw_items, list) or not raw_items:
            return Response(
                {"status": "error", "message": "Provide at least one priced item."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parsed = []
        for index, line in enumerate(raw_items):
            if not isinstance(line, dict):
                return Response(
                    {"status": "error", "message": f"Item {index} is malformed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            name = str(line.get('name') or '').strip() or 'Item'
            try:
                quantity = int(line.get('quantity', 1))
                price = Decimal(str(line.get('price')))
            except (TypeError, ValueError, InvalidOperation):
                return Response(
                    {"status": "error", "message": f"Item {index} has an invalid quantity or price."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if quantity < 1 or price < 0:
                return Response(
                    {"status": "error", "message": f"Item {index} has an invalid quantity or price."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            parsed.append((name, quantity, price))

        with transaction.atomic():
            for name, quantity, price in parsed:
                OrderItem.objects.create(
                    order=order, item=None, service_type=None,
                    name=name, quantity=quantity, price=price,
                )
            # Freeze the invoice so later reads and settlement use these figures.
            FinanceService.freeze_price_breakdown(order, coupon=order.coupon)

        NotificationService.notify_user(
            user=order.user,
            title="Your invoice is ready",
            body=(
                f"Your laundry has been quoted GHS {order.total_amount} for order {order.order_no}. "
                + ("Pay cash at delivery." if order.payment_method == Order.PaymentMethod.CASH else "Tap to review and pay.")
            ),
            type=Notification.Type.ORDER,
            category="QUOTE_READY",
            related_order=order,
            dedup_key=f"quote_ready_{order.id}",
        )

        return Response({
            "status": "success",
            "message": "Quote sent to the customer.",
            "data": OrderDetailSerializer(order).data,
        })

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
        
