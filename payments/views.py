import uuid
import logging
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from .models import Payment
from .services.paystack import PaystackService
from ordering.models import Order

logger = logging.getLogger(__name__)


class PaymentInitializeView(APIView):
    """
    POST /api/v1/payments/initialize/
    Initiates a new payment session with Paystack.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "burst_user"

    def post(self, request):
        order_id = request.data.get("order_id")
        payment_method = request.data.get("payment_method", "CARD")

        # 1. Validate order existence and ownership
        order = get_object_or_404(Order, id=order_id, user=request.user)

        # 2. Reject if status is invalid for payment
        if order.status not in [Order.Status.PENDING, Order.Status.WEIGHED]:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": f"Cannot pay for order in {order.status} status.",
                    "data": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Calculate amount to pay
        # For PER_KG, if status is PENDING, pay estimated. If WEIGHED, pay final or balance.
        # Simplification: If WEIGHED, we charge final_price. If PENDING, we
        # charge estimated_price.
        amount = (
            order.final_price
            if order.status == Order.Status.WEIGHED
            else order.estimated_price
        )

        # 4. Reject if successful payment for this amount already exists
        # (simple check)
        if Payment.objects.filter(
            order=order, status=Payment.Status.SUCCESS, amount=amount
        ).exists():
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": "This payment has already been completed.",
                    "data": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 5. Generate unique reference
        reference = f"ORD-{uuid.uuid4().hex[:10].upper()}"

        paystack = PaystackService()
        metadata = {
            "order_id": str(order.id),
            "user_id": str(request.user.id),
            "order_no": order.order_no,
            "payment_type": (
                "FINAL" if order.status == Order.Status.WEIGHED else "INITIAL"
            ),
        }

        # 6. Initialize with Paystack
        response = paystack.initialize_transaction(
            email=request.user.email,
            amount=amount,
            reference=reference,
            metadata=metadata,
        )

        if response.get("status"):
            # 7. Atomic creation of Payment record
            with transaction.atomic():
                Payment.objects.create(
                    user=request.user,
                    order=order,
                    amount=amount,
                    currency=getattr(settings, "CURRENCY", "GHS"),
                    transaction_reference=reference,
                    payment_method=payment_method,
                    status=Payment.Status.PENDING,
                    paystack_reference=response["data"]["access_code"],
                )

            return Response(
                {
                    "success": True,
                    "message": "Payment initialized successfully",
                    "data": {
                        "authorization_url": response["data"]["authorization_url"],
                        "reference": reference,
                    },
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "success": False,
                "status": "error",
                "message": response.get("message", "Payment initialization failed."),
                "data": {},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class PaymentVerifyView(APIView):
    """
    GET /api/v1/payments/verify/{reference}/
    Verifies a payment status with Paystack.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, reference):
        paystack = PaystackService()
        verify_data = paystack.verify_transaction(reference)

        if verify_data.get("status") and verify_data["data"]["status"] == "success":
            # 7. Use select_for_update() and transaction.atomic()
            with transaction.atomic():
                payment = (
                    Payment.objects.select_for_update()
                    .filter(transaction_reference=reference)
                    .first()
                )

                if not payment:
                    return Response(
                        {
                            "success": False,
                            "status": "error",
                            "message": "Payment record not found.",
                            "data": {},
                        },
                        status=status.HTTP_404_NOT_FOUND,
                    )

                if payment.status != Payment.Status.SUCCESS:
                    payment.status = Payment.Status.SUCCESS
                    payment.raw_response = verify_data["data"]
                    payment.paid_at = timezone.now()
                    payment.save()

                    # Update order status based on payment stage
                    order = payment.order
                    if order.status == Order.Status.WEIGHED:
                        order.status = Order.Status.IN_PROCESS
                        order.payment_status = Order.PaymentStatus.PAID
                        order.processing_started_at = timezone.now()
                    elif order.status == Order.Status.PENDING:
                        order.status = Order.Status.CONFIRMED
                        # If PER_KG, it's only partially paid (estimated)
                        if order.pricing_method == "PER_KG":
                            order.payment_status = Order.PaymentStatus.PARTIALLY_PAID
                        else:
                            order.payment_status = Order.PaymentStatus.PAID
                        order.confirmed_at = timezone.now()

                    order.save()

            return Response(
                {
                    "success": True,
                    "message": f"Payment verified. Order status is now {order.status}.",
                    "data": {
                        "payment_status": payment.status,
                        "order_status": order.status,
                    },
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "success": False,
                "status": "error",
                "message": verify_data.get("message", "Payment verification failed."),
                "data": {},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
