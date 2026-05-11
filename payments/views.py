import uuid
import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Payment
from .services.paystack import PaystackService
from ordering.models import Order
from config.throttling import PaymentCreateThrottle

logger = logging.getLogger(__name__)


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

    if expected_minor is None or amount_minor != expected_minor:
        return False, 'Payment amount verification failed.'

    if currency != expected_currency:
        return False, 'Payment currency verification failed.'

    metadata_order_id = str(metadata.get('order_id') or '')
    metadata_user_id = str(metadata.get('user_id') or '')
    if metadata_order_id and metadata_order_id != str(payment.order_id):
        return False, 'Payment order verification failed.'
    if metadata_user_id and metadata_user_id != str(payment.user_id):
        return False, 'Payment user verification failed.'

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

    def post(self, request):
        order_id = request.data.get('order_id')
        payment_method = _normalize_payment_method(request.data.get('payment_method', 'CARD'))
        
        # 1. Validate order existence and ownership
        order = get_object_or_404(Order, id=order_id, user=request.user)
        
        # 2. Reject if status is not PENDING
        if order.status != Order.Status.PENDING:
            return Response({
                "status": "error",
                "message": f"Cannot pay for order in {order.status} status.",
                "data": {}
            }, status=status.HTTP_400_BAD_REQUEST)
            
        # 3. Reject if successful payment already exists
        if Payment.objects.filter(order=order, status=Payment.Status.SUCCESS).exists():
             return Response({
                "status": "error",
                "message": "Order is already paid.",
                "data": {}
            }, status=status.HTTP_400_BAD_REQUEST)

        # 4. Generate unique reference
        reference = f"ORD-{uuid.uuid4().hex[:10].upper()}"

        if payment_method == Payment.Method.CASH:
            with transaction.atomic():
                Payment.objects.update_or_create(
                    order=order,
                    defaults={
                        'user': request.user,
                        'amount': order.total_amount,
                        'currency': settings.PAYMENT_CURRENCY,
                        'transaction_reference': f"COD-{uuid.uuid4().hex[:10].upper()}",
                        'payment_method': Payment.Method.CASH,
                        'status': Payment.Status.PENDING,
                        'paystack_reference': None,
                    },
                )

            return Response({
                "status": "success",
                "message": "Cash payment recorded successfully",
                "data": {
                    "authorization_url": None,
                    "reference": None,
                }
            }, status=status.HTTP_200_OK)
        
        paystack = PaystackService()
        metadata = {
            "order_id": str(order.id),
            "user_id": str(request.user.id),
            "order_no": order.order_no
        }
        
        # 5. Initialize with Paystack
        response = paystack.initialize_transaction(
            email=request.user.email,
            amount=order.total_amount,
            reference=reference,
            metadata=metadata
        )
        
        if response.get('status'):
            # 6. Atomic creation of Payment record
            with transaction.atomic():
                Payment.objects.create(
                    user=request.user,
                    order=order,
                    amount=order.total_amount,
                    currency=settings.PAYMENT_CURRENCY,
                    transaction_reference=reference,
                    payment_method=payment_method,
                    status=Payment.Status.PENDING,
                    paystack_reference=response['data']['access_code']
                )
            
            return Response({
                "status": "success",
                "message": "Payment initialized successfully",
                "data": {
                    "authorization_url": response['data']['authorization_url'],
                    "reference": reference
                }
            }, status=status.HTTP_200_OK)
            
        return Response({
            "status": "error",
            "message": response.get('message', 'Payment initialization failed.'),
            "data": {}
        }, status=status.HTTP_400_BAD_REQUEST)

class PaymentVerifyView(APIView):
    """
    GET /api/v1/payments/verify/{reference}/
    Verifies a payment status with Paystack.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, reference):
        paystack = PaystackService()
        verify_data = paystack.verify_transaction(reference)
        
        if verify_data.get('status') and verify_data['data']['status'] == 'success':
            # 7. Use select_for_update() and transaction.atomic()
            with transaction.atomic():
                payment = Payment.objects.select_for_update().filter(transaction_reference=reference).first()
                
                if not payment:
                    return Response({
                        "status": "error", 
                        "message": "Payment record not found.",
                        "data": {}
                    }, status=status.HTTP_404_NOT_FOUND)

                if payment.status != Payment.Status.SUCCESS:
                    is_valid, validation_error = _validate_verified_payment(payment, verify_data.get('data', {}))
                    if not is_valid:
                        payment.status = Payment.Status.FAILED
                        payment.raw_response = _sanitize_payment_response(verify_data.get('data', {}), reference)
                        payment.save(update_fields=['status', 'raw_response', 'updated_at'])
                        return Response({
                            "status": "error",
                            "message": validation_error,
                            "data": {}
                        }, status=status.HTTP_400_BAD_REQUEST)

                    payment.status = Payment.Status.SUCCESS
                    payment.raw_response = _sanitize_payment_response(verify_data.get('data', {}), reference)
                    payment.paid_at = timezone.now()
                    payment.save()
                    
                    # Update order status
                    order = payment.order
                    order.status = Order.Status.CONFIRMED
                    order.payment_status = Order.PaymentStatus.PAID
                    order.save(update_fields=['status', 'payment_status', 'updated_at'])
            
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
