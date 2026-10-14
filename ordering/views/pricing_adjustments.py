"""Customer consent for measured weight and overfilled baskets.

Only unpaid orders without a provider attempt can be revised. The existing
one-payment-per-order ledger cannot safely charge a paid-order surcharge;
those requests require the existing refund/rebook support flow.
"""
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from ordering.models import Order, OrderItem, PriceAdjustment
from ordering.serializers.order import OrderItemCreateSerializer
from ordering.services.catalog_pricing import enabled, price_lines
from ordering.services.finance_service import FinanceService


def pending_adjustment(order):
    # Expiry is a state transition, not an indefinite fulfillment hold.
    order.pricing_adjustments.filter(status='PENDING', expires_at__lte=timezone.now()).update(status='EXPIRED')
    return order.pricing_adjustments.filter(status='PENDING').exists()


def mutable_order(order):
    from payments.models import Payment
    if order.status in {'DELIVERED', 'COMPLETED', 'CANCELLED', 'REJECTED'}:
        raise ValidationError({'code': 'ORDER_CLOSED', 'message': 'This order can no longer be adjusted.'})
    if order.payment_status != Order.PaymentStatus.UNPAID or Payment.objects.filter(order=order).exists():
        raise ValidationError({'code': 'PAYMENT_ADJUSTMENT_UNAVAILABLE', 'message': 'Payment has already started. Contact support to refund and rebook; no extra charge has been made.'})
    if order.coupon_id:
        raise ValidationError({'code': 'COUPON_ADJUSTMENT_UNAVAILABLE', 'message': 'Contact support to revise an order with a redeemed promotion.'})


def serialize(proposal):
    return {'id': str(proposal.id), 'reason': proposal.reason, 'status': proposal.status,
            'original_total': str(proposal.original_total), 'proposed_total': str(proposal.proposed_total),
            'additional_amount': str(proposal.proposed_total - proposal.original_total), 'currency': 'GHS',
            'lines': proposal.lines, 'breakdown': proposal.breakdown,
            'expires_at': proposal.expires_at.isoformat(), 'created_at': proposal.created_at.isoformat()}


class ProposalSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)
    items = OrderItemCreateSerializer(many=True, allow_empty=False)


class PricingAdjustmentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def order(self, request, pk, lock=False):
        qs = Order.objects.filter(Q(user=request.user) | Q(laundry__owner=request.user)).select_related('laundry')
        if lock:
            qs = qs.select_for_update(of=('self',))
        return get_object_or_404(qs, pk=pk)

    def get(self, request, pk):
        order = self.order(request, pk)
        pending_adjustment(order)
        return Response({'data': [serialize(p) for p in order.pricing_adjustments.all()[:50]]})

    @transaction.atomic
    def post(self, request, pk):
        order = self.order(request, pk, lock=True)
        if order.laundry.owner_id != request.user.pk:
            return Response({'message': 'Only this laundry can propose an adjustment.'}, status=403)
        if not enabled():
            raise ValidationError('Pricing adjustments are not enabled yet.')
        mutable_order(order)
        if pending_adjustment(order):
            raise ValidationError('Wait for the customer to respond to the pending proposal.')
        payload = ProposalSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lines, subtotal = price_lines(order.laundry, payload.validated_data['items'], contract=2, lock=True)
        # Keep original transport; this is a service adjustment, not another trip.
        tax = FinanceService.calculate_tax_amount(subtotal)
        platform = FinanceService.calculate_platform_fee(subtotal)
        total = (subtotal + tax + platform + order.pickup_fee + order.delivery_fee).quantize(Decimal('0.01'))
        if total > Decimal('999999.99'):
            raise ValidationError('The revised total exceeds the order limit.')
        snapshots = []
        for line in lines:
            snap = line['pricing_snapshot']
            if snap.get('weight_kg'):
                snap['weight_status'] = 'MEASURED'
            snapshots.append({**snap, 'charged_unit_price': str(line['price']),
                              'legacy_item_id': str(line['item'].pk) if line['item'] else None,
                              'legacy_service_type_id': str(line['service_type'].pk) if line['service_type'] else None})
        proposal = PriceAdjustment.objects.create(
            order=order, requested_by=request.user, reason=payload.validated_data['reason'],
            original_total=order.total_amount, proposed_total=total,
            original_lines=[{'name': i.name, 'quantity': i.quantity, 'price': str(i.price), 'pricing_snapshot': i.pricing_snapshot} for i in order.items.all()],
            lines=snapshots, breakdown={'items_total': str(subtotal), 'tax_amount': str(tax), 'platform_fee': str(platform),
                                       'discount_amount': '0.00', 'total_amount': str(total)},
            expires_at=timezone.now() + timedelta(hours=24))
        from marketplace.services.notification_service import NotificationService
        from marketplace.models import Notification
        NotificationService.notify_user(
            user=order.user, title='Review your laundry price',
            body=f'{order.laundry.name} proposed GHS {total} for {order.order_no}. Review and approve before the price changes.',
            type=Notification.Type.ORDER, category='PRICE_ADJUSTMENT', related_order=order,
            dedup_key=f'price_adjustment_{proposal.id}')
        return Response({'data': serialize(proposal)}, status=201)


class PricingAdjustmentResponseView(PricingAdjustmentView):
    @transaction.atomic
    def post(self, request, pk, adjustment_id):
        order = self.order(request, pk, lock=True)
        if order.user_id != request.user.pk:
            return Response({'message': 'Only the customer can approve or reject a price.'}, status=403)
        proposal = get_object_or_404(PriceAdjustment.objects.select_for_update(), pk=adjustment_id, order=order)
        decision = request.data.get('decision')
        if decision not in {'APPROVE', 'REJECT'}:
            raise ValidationError({'decision': 'Choose APPROVE or REJECT.'})
        target = 'APPROVED' if decision == 'APPROVE' else 'REJECTED'
        if proposal.status == target:
            return Response({'data': serialize(proposal)})
        if proposal.status != 'PENDING':
            raise ValidationError('This proposal has already been answered.')
        if proposal.expires_at <= timezone.now():
            proposal.status = 'EXPIRED'
            proposal.save(update_fields=['status'])
            return Response({'message': 'This proposal has expired.', 'data': serialize(proposal)}, status=409)
        if decision == 'APPROVE':
            mutable_order(order)
            if order.total_amount != proposal.original_total:
                raise ValidationError({'code': 'STALE_QUOTE', 'message': 'The original order has changed.'})
            order.items.all().delete()
            OrderItem.objects.bulk_create([OrderItem(
                order=order, name=line['name'][:100], quantity=line['quantity'], price=Decimal(line['charged_unit_price']),
                item_id=line.get('legacy_item_id'), service_type_id=line.get('legacy_service_type_id'), pricing_snapshot=line)
                for line in proposal.lines])
            for key, value in proposal.breakdown.items():
                setattr(order, key, Decimal(value))
            order.priced_at = timezone.now()
            order.save(update_fields=[*proposal.breakdown, 'priced_at', 'updated_at'])
        proposal.status = target
        proposal.responded_at = timezone.now()
        proposal.save(update_fields=['status', 'responded_at'])
        return Response({'data': serialize(proposal)})
