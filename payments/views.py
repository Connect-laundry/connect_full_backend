import uuid
import logging
from decimal import Decimal
from urllib.parse import urlencode
from django.db import transaction
from django.db.models import Avg, Sum, Count
from django.utils import timezone
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from rest_framework import serializers, status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from .models import Payment
from .services.paystack import PaystackService
from .services.receipt import ReceiptService
from ordering.models import Order
from config.throttling import PaymentCreateThrottle
from marketplace.services.audit import record_audit
from ordering.services.order_state_machine import OrderStateMachine
from marketplace.services.notification_service import NotificationService
from marketplace.models import Notification
from laundries.models.laundry import Laundry

logger = logging.getLogger(__name__)


class PaymentInitializeRequestSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    payment_method = serializers.ChoiceField(
        choices=['CARD', 'PAYSTACK', 'BANK_TRANSFER', 'TRANSFER'],
        required=False,
        default='CARD',
    )


class PaymentInitializeDataSerializer(serializers.Serializer):
    authorization_url = serializers.URLField(allow_null=True)
    reference = serializers.CharField(allow_null=True)


class PaymentInitializeResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = PaymentInitializeDataSerializer()


class PaymentVerifyDataSerializer(serializers.Serializer):
    payment_status = serializers.CharField()
    order_status = serializers.CharField()
    order_payment_status = serializers.CharField()


class PaymentVerifyResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = PaymentVerifyDataSerializer()


class ReceiptLaundrySerializer(serializers.Serializer):
    name = serializers.CharField()
    address = serializers.CharField()
    phone_number = serializers.CharField()


class ReceiptCustomerSerializer(serializers.Serializer):
    name = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField()


class ReceiptItemSerializer(serializers.Serializer):
    name = serializers.CharField()
    service_type = serializers.CharField()
    quantity = serializers.IntegerField()
    price = serializers.CharField()
    total = serializers.CharField()


class ReceiptPricingSerializer(serializers.Serializer):
    items_subtotal = serializers.CharField()
    total_amount = serializers.CharField()
    currency = serializers.CharField()


class ReceiptPaymentSerializer(serializers.Serializer):
    method = serializers.CharField()
    status = serializers.CharField()
    paid_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class ReceiptResponseSerializer(serializers.Serializer):
    receipt_no = serializers.CharField()
    transaction_reference = serializers.CharField()
    order_no = serializers.CharField()
    laundry = ReceiptLaundrySerializer()
    customer = ReceiptCustomerSerializer()
    items = ReceiptItemSerializer(many=True)
    pricing = ReceiptPricingSerializer()
    payment = ReceiptPaymentSerializer()


class AnalyticsPaymentMethodBreakdownSerializer(serializers.Serializer):
    payment_method = serializers.CharField()
    count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)


class AnalyticsResponseSerializer(serializers.Serializer):
    total_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    success_rate = serializers.FloatField()
    count_success = serializers.IntegerField()
    count_failed = serializers.IntegerField()
    count_pending = serializers.IntegerField()
    average_order_value = serializers.DecimalField(max_digits=12, decimal_places=2)
    method_breakdown = AnalyticsPaymentMethodBreakdownSerializer(many=True)


class OwnerStatsResponseSerializer(serializers.Serializer):
    total_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    count_success = serializers.IntegerField()
    count_failed = serializers.IntegerField()
    count_pending = serializers.IntegerField()
    method_breakdown = AnalyticsPaymentMethodBreakdownSerializer(many=True)


def _sanitize_payment_response(payload, reference):
    data = payload if isinstance(payload, dict) else {}
    return {
        "reference": reference,
        "status": data.get("status"),
        "gateway_status": data.get("gateway_response") or data.get("gateway_status"),
        "amount": data.get("amount"),
        "currency": data.get("currency", "GHS"),
    }


def _to_minor_units(amount):
    try:
        return int((Decimal(str(amount)) * 100).quantize(Decimal('1')))
    except Exception:
        return None


def _validate_verified_payment(payment, payload):
    data = payload if isinstance(payload, dict) else {}
    amount_minor = data.get('amount')
    currency = str(data.get('currency') or '').upper()
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}

    expected_minor = _to_minor_units(payment.amount)
    expected_currency = str(payment.currency or settings.PAYMENT_CURRENCY).upper()

    if str(data.get('reference') or '') != payment.transaction_reference:
        return False, 'Payment reference verification failed.'

    if expected_minor is None or amount_minor != expected_minor:
        return False, 'Payment amount verification failed.'

    if currency != expected_currency:
        return False, 'Payment currency verification failed.'

    metadata_order_id = str(metadata.get('order_id') or '')
    metadata_user_id = str(metadata.get('user_id') or '')
    if metadata_order_id != str(payment.order_id):
        return False, 'Payment order verification failed.'
    if metadata_user_id != str(payment.user_id):
        return False, 'Payment user verification failed.'

    provider_domain = str(data.get('domain') or '').lower()
    secret_key = str(settings.PAYSTACK_SECRET_KEY or '')
    if secret_key.startswith('sk_live_') and provider_domain != 'live':
        return False, 'Payment environment verification failed.'
    if secret_key.startswith('sk_test_') and provider_domain != 'test':
        return False, 'Payment environment verification failed.'

    return True, None


def _normalize_payment_method(payment_method):
    normalized = str(payment_method or 'CARD').strip().upper()
    if normalized in {'PAYSTACK', 'CARD'}:
        return Payment.Method.CARD
    if normalized in {'CASH', 'CASH_ON_DELIVERY'}:
        return Payment.Method.CASH
    if normalized in {'BANK_TRANSFER', 'TRANSFER'}:
        return Payment.Method.TRANS
    return Payment.Method.CARD


class PaymentInitializeView(APIView):
    """
    POST /api/v1/payments/initialize/
    Initiates a new payment session with Paystack.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PaymentCreateThrottle]

    @extend_schema(
        request=PaymentInitializeRequestSerializer,
        responses=PaymentInitializeResponseSerializer,
    )
    @transaction.atomic
    def post(self, request):
        request_serializer = PaymentInitializeRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        order_id = request_serializer.validated_data['order_id']
        payment_method = _normalize_payment_method(
            request_serializer.validated_data.get('payment_method', 'CARD')
        )

        # 1. Validate order existence and ownership
        order = get_object_or_404(
            Order.objects.select_for_update(), id=order_id, user=request.user
        )
        
        # Accepted custom quotes remain payable; ordinary online orders must
        # still pay before the laundry can accept them.
        accepted_quote = (
            order.pricing_mode == Order.PricingMode.CUSTOM_QUOTE
            and order.status == Order.Status.CONFIRMED
        )
        if order.status != Order.Status.PENDING and not accepted_quote:
            return Response({
                "status": "error",
                "message": f"Cannot pay for order in {order.status} status.",
                "data": {}
            }, status=status.HTTP_400_BAD_REQUEST)

        if (
            order.pricing_mode == Order.PricingMode.CUSTOM_QUOTE
            and (order.priced_at is None or order.total_amount <= 0)
        ):
            return Response({
                "status": "error",
                "message": "This order is still waiting for its quote.",
                "data": {},
            }, status=status.HTTP_409_CONFLICT)

        if order.payment_method == Order.PaymentMethod.CASH:
            return Response({
                "status": "error",
                "message": "This is a cash-on-delivery order. Payment is collected at fulfillment.",
                "data": {},
            }, status=status.HTTP_409_CONFLICT)

        if payment_method != order.payment_method:
            return Response({
                "status": "error",
                "message": "Payment method does not match the method selected for this order.",
                "data": {},
            }, status=status.HTTP_409_CONFLICT)
        # 3. Reject if successful payment already exists
        if Payment.objects.filter(order=order, status=Payment.Status.SUCCESS).exists():
             return Response({
                "status": "error",
                "message": "Order is already paid.",
                "data": {}
            }, status=status.HTTP_400_BAD_REQUEST)

        # 4. Idempotency Check: reuse pending payment if same method exists
        existing_payment = Payment.objects.filter(
            order=order,
            status=Payment.Status.PENDING,
            payment_method=payment_method
        ).first()
        
        if existing_payment and existing_payment.paystack_reference:
            # Never replace a still-pending provider reference. A second charge
            # could later succeed after this row was overwritten, leaving real
            # money with no payment record to reconcile.
            authorization_url = f"https://checkout.paystack.com/{existing_payment.paystack_reference}"

            record_audit(
                action="PAYMENT_INITIALIZED_REUSED",
                actor=request.user,
                request=request,
                target_type="Payment",
                target_id=str(existing_payment.id),
                target_repr=f"Payment {existing_payment.transaction_reference} Reused",
                metadata={
                    "amount": str(order.total_amount),
                    "method": payment_method,
                    "reference": existing_payment.transaction_reference
                }
            )

            return Response({
                "status": "success",
                "message": "Existing payment session resumed successfully",
                "data": {
                    "authorization_url": authorization_url,
                    "reference": existing_payment.transaction_reference
                }
            }, status=status.HTTP_200_OK)

        # 5. Generate unique reference
        reference = f"ORD-{uuid.uuid4().hex[:10].upper()}"

        paystack = PaystackService()
        metadata = {
            "booking_id": str(order.id),
            "order_id": str(order.id),
            "user_id": str(request.user.id),
            "laundry_id": str(order.laundry_id),
            "order_no": order.order_no,
            "environment": "development" if settings.DEBUG else "production",
            "payment_method": payment_method
        }
        
        # 6. Initialize with Paystack
        response = paystack.initialize_transaction(
            email=request.user.email,
            amount=order.total_amount,
            reference=reference,
            metadata=metadata
        )
        
        response_data = response.get('data') if isinstance(response.get('data'), dict) else {}
        if response.get('status') and response_data.get('access_code') and response_data.get('authorization_url'):
            # 7. Atomic creation of Payment record
            with transaction.atomic():
                payment, _ = Payment.objects.update_or_create(
                    order=order,
                    defaults={
                        'user': request.user,
                        'amount': order.total_amount,
                        'currency': settings.PAYMENT_CURRENCY,
                        'transaction_reference': reference,
                        'payment_method': payment_method,
                        'status': Payment.Status.PENDING,
                        'paystack_reference': response_data['access_code'],
                    },
                )
                
            record_audit(
                action="PAYMENT_INITIALIZED",
                actor=request.user,
                request=request,
                target_type="Payment",
                target_id=str(payment.id),
                target_repr=f"Payment {reference} Initialized",
                metadata={
                    "amount": str(order.total_amount),
                    "method": payment_method,
                    "environment": metadata["environment"]
                }
            )
            
            return Response({
                "status": "success",
                "message": "Payment initialized successfully",
                "data": {
                    "authorization_url": response_data['authorization_url'],
                    "reference": reference
                }
            }, status=status.HTTP_200_OK)

        if response.get('status'):
            # Paystack said OK but the payload is missing checkout fields —
            # treat as a provider fault, not a client error.
            logger.error(
                "Paystack initialization returned incomplete payload",
                extra={"order_id": str(order.id)},
            )
        return Response({
            "status": "error",
            "message": response.get('message', 'Payment initialization failed.'),
            "data": {}
        }, status=status.HTTP_400_BAD_REQUEST)


class MobileAppRedirect(HttpResponseRedirect):
    allowed_schemes = ['connect-laundry']


def payment_callback(request):
    """Bridge Paystack's HTTPS callback back into the installed mobile app."""
    reference = request.GET.get('reference') or request.GET.get('trxref')
    if not reference:
        return HttpResponseBadRequest('Payment reference is required.')

    app_callback = str(settings.PAYSTACK_APP_CALLBACK_URL or '')
    if not app_callback.startswith('connect-laundry://'):
        logger.error('PAYSTACK_APP_CALLBACK_URL is not a permitted Simame app URL.')
        return HttpResponseBadRequest('Payment callback is unavailable.')

    separator = '&' if '?' in app_callback else '?'
    location = f"{app_callback}{separator}{urlencode({'reference': reference, 'trxref': reference})}"
    return MobileAppRedirect(location)

class PaymentVerifyView(APIView):
    """
    GET /api/v1/payments/verify/{reference}/
    Verifies a payment status with Paystack.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=PaymentVerifyResponseSerializer)
    def get(self, request, reference):
        owned_payment = get_object_or_404(
            Payment, transaction_reference=reference, user=request.user
        )
        paystack = PaystackService()
        verify_data = paystack.verify_transaction(reference)

        verify_payload = verify_data.get('data') if isinstance(verify_data.get('data'), dict) else {}
        if verify_data.get('status') and verify_payload.get('status') == 'success':
            # Use select_for_update() and transaction.atomic()
            with transaction.atomic():
                payment = Payment.objects.select_for_update().filter(pk=owned_payment.pk).first()

                if not payment:
                    return Response({
                        "status": "error",
                        "message": "Payment record not found.",
                        "data": {}
                    }, status=status.HTTP_404_NOT_FOUND)

                if payment.user_id != request.user.id:
                    return Response({
                        "status": "error",
                        "message": "Payment record not found.",
                        "data": {}
                    }, status=status.HTTP_404_NOT_FOUND)

                if payment.status != Payment.Status.SUCCESS:
                    is_valid, validation_error = _validate_verified_payment(payment, verify_data.get('data', {}))
                    if not is_valid:
                        payment.transition_to(Payment.Status.FAILED, save=False)
                        payment.raw_response = _sanitize_payment_response(verify_data.get('data', {}), reference)
                        payment.save(update_fields=['status', 'raw_response', 'updated_at'])
                        
                        record_audit(
                            action="PAYMENT_VERIFICATION_FAILED",
                            actor=request.user,
                            request=request,
                            target_type="Payment",
                            target_id=str(payment.id),
                            target_repr=f"Payment {reference} Verification Failed",
                            metadata={"reason": validation_error, "amount": str(payment.amount)}
                        )
                        
                        NotificationService.notify_user(
                            user=payment.user,
                            title="Payment Failed",
                            body=f"Your payment attempt for order {payment.order.order_no} failed: {validation_error}.",
                            type=Notification.Type.ORDER,
                            category="PAYMENT_FAILED",
                            related_order=payment.order,
                            dedup_key=f"pay_failed_{payment.id}"
                        )
                        
                        return Response({
                            "status": "error",
                            "message": validation_error,
                            "data": {}
                        }, status=status.HTTP_400_BAD_REQUEST)

                    payment.transition_to(Payment.Status.SUCCESS, save=False)
                    payment.raw_response = _sanitize_payment_response(verify_data.get('data', {}), reference)
                    payment.paid_at = timezone.now()
                    
                    # Update payment method dynamically from Paystack gateway channel
                    channel = verify_data.get('data', {}).get('channel')
                    if channel == 'mobile_money':
                        payment.payment_method = Payment.Method.MOMO
                    elif channel == 'card':
                        payment.payment_method = Payment.Method.CARD
                    elif channel in {'bank', 'bank_transfer', 'transfer'}:
                        payment.payment_method = Payment.Method.TRANS
                    
                    payment.save()
                    
                    # Update order payment status
                    order = payment.order
                    order.payment_status = Order.PaymentStatus.PAID
                    order.save(update_fields=['payment_status', 'updated_at'])

                    from .webhooks import _paystack_fee
                    from .services.settlement_service import SettlementService
                    SettlementService.record_for_order(
                        order,
                        processor_fee=_paystack_fee(verify_data.get('data', {})),
                        settled_directly=payment.settled_directly,
                    )
                    
                    # Transition order using OrderStateMachine to trigger signal pipeline
                    OrderStateMachine.transition(order.id, Order.Status.CONFIRMED, user=request.user)
                    
                    record_audit(
                        action="PAYMENT_VERIFIED",
                        actor=request.user,
                        request=request,
                        target_type="Payment",
                        target_id=str(payment.id),
                        target_repr=f"Payment {reference} Confirmed",
                        metadata={"amount": str(payment.amount), "order_id": str(order.id)}
                    )
                    
                    NotificationService.notify_user(
                        user=payment.user,
                        title="Payment Successful",
                        body=f"Your payment of GHS {payment.amount} for order {order.order_no} was successful.",
                        type=Notification.Type.ORDER,
                        category="PAYMENT_SUCCESS",
                        related_order=order,
                        dedup_key=f"pay_success_{payment.id}"
                    )
            
            return Response({
                "status": "success", 
                "message": "Payment verified and order confirmed.",
                "data": {
                    "payment_status": payment.status,
                    "order_status": payment.order.status,
                    "order_payment_status": payment.order.payment_status,
                }
            }, status=status.HTTP_200_OK)
            
        return Response({
            "status": "error", 
            "message": verify_data.get('message', "Payment verification failed."),
            "data": {}
        }, status=status.HTTP_400_BAD_REQUEST)


class PaymentStatusView(APIView):
    """
    GET /api/v1/payments/status/{reference}/
    Retrieves the database status of the payment.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=PaymentVerifyResponseSerializer)
    def get(self, request, reference):
        payment = get_object_or_404(Payment, transaction_reference=reference, user=request.user)
        return Response({
            "status": "success",
            "message": "Payment status retrieved successfully.",
            "data": {
                "reference": payment.transaction_reference,
                "payment_status": payment.status,
                "order_status": payment.order.status,
                "order_payment_status": payment.order.payment_status,
            }
        }, status=status.HTTP_200_OK)


class PaymentReceiptView(APIView):
    """
    GET /api/v1/payments/receipt/{reference}/
    Compiles structured receipt info.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=ReceiptResponseSerializer)
    def get(self, request, reference):
        payment = get_object_or_404(Payment, transaction_reference=reference)
        # Auth verification: must be owner of payment or system admin
        if payment.user_id != request.user.id and request.user.role != 'ADMIN' and not request.user.is_staff:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        data = ReceiptService.compile_receipt_data(payment)
        return Response(data, status=status.HTTP_200_OK)


class PaymentAnalyticsView(APIView):
    """
    GET /api/v1/payments/analytics/
    Admin metrics on payments.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=AnalyticsResponseSerializer)
    def get(self, request):
        # Admin access check
        if request.user.role != 'ADMIN' and not request.user.is_staff:
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        payments = Payment.objects.all()
        total_count = payments.count()
        success_count = payments.filter(status=Payment.Status.SUCCESS).count()
        
        success_rate = (success_count / total_count * 100) if total_count > 0 else 100.0
        
        total_revenue = payments.filter(status=Payment.Status.SUCCESS).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        avg_value = payments.filter(status=Payment.Status.SUCCESS).aggregate(avg=Avg('amount'))['avg'] or Decimal('0.00')
        
        method_data = payments.filter(status=Payment.Status.SUCCESS).values('payment_method').annotate(
            count=Count('id'),
            total_amount=Sum('amount')
        )
        
        method_breakdown = []
        for item in method_data:
            method_breakdown.append({
                "payment_method": item['payment_method'],
                "count": item['count'],
                "total_amount": item['total_amount']
            })

        return Response({
            "total_revenue": total_revenue,
            "success_rate": success_rate,
            "count_success": success_count,
            "count_failed": payments.filter(status=Payment.Status.FAILED).count(),
            "count_pending": payments.filter(status=Payment.Status.PENDING).count(),
            "average_order_value": avg_value,
            "method_breakdown": method_breakdown
        }, status=status.HTTP_200_OK)


class PaymentOwnerStatsView(APIView):
    """
    GET /api/v1/payments/owner-stats/
    Financial dashboard stats for laundry owners.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=OwnerStatsResponseSerializer)
    def get(self, request):
        if request.user.role != 'OWNER':
            return Response({"detail": "Permission denied. Only laundry owners can view this."}, status=status.HTTP_403_FORBIDDEN)

        laundries = Laundry.objects.filter(owner=request.user)
        payments = Payment.objects.filter(order__laundry__in=laundries)
        
        total_revenue = payments.filter(status=Payment.Status.SUCCESS).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        method_data = payments.filter(status=Payment.Status.SUCCESS).values('payment_method').annotate(
            count=Count('id'),
            total_amount=Sum('amount')
        )
        
        method_breakdown = []
        for item in method_data:
            method_breakdown.append({
                "payment_method": item['payment_method'],
                "count": item['count'],
                "total_amount": item['total_amount']
            })

        return Response({
            "total_revenue": total_revenue,
            "count_success": payments.filter(status=Payment.Status.SUCCESS).count(),
            "count_failed": payments.filter(status=Payment.Status.FAILED).count(),
            "count_pending": payments.filter(status=Payment.Status.PENDING).count(),
            "method_breakdown": method_breakdown
        }, status=status.HTTP_200_OK)


class PaymentRefundSerializer(serializers.Serializer):
    """Body for POST /payments/refund/{reference}/."""
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True,
        help_text='Partial refund amount in GHS. Omit for a full refund.',
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)


class PaymentRefundView(APIView):
    """
    POST /api/v1/payments/refund/{reference}/

    Staff-only. Starts a refund with Paystack; the payment moves to
    REFUND_PENDING and reaches REFUNDED when the `refund.processed` webhook
    lands. Refunds are money-moving and irreversible, so this is never
    exposed to customers or laundry owners.
    """
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    @extend_schema(request=PaymentRefundSerializer, responses=None)
    def post(self, request, reference):
        from .services.refund import RefundError, refund_payment

        serializer = PaymentRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payment = get_object_or_404(Payment, transaction_reference=reference)

        try:
            refunded = refund_payment(
                payment,
                amount=serializer.validated_data.get('amount'),
                reason=serializer.validated_data.get('reason', ''),
                actor=request.user,
                request=request,
            )
        except RefundError as exc:
            return Response(
                {"status": "error", "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": "success",
            "message": "Refund requested. It will settle shortly.",
            "data": {
                "payment_status": refunded.status,
                "order_id": str(refunded.order_id),
            },
        })
