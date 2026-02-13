from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from .models import Payment
from .services.paystack import PaystackService
from ordering.models import Order

class PaymentInitializeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        order_id = request.data.get('order_id')
        payment_method = request.data.get('payment_method', 'CARD')
        
        order = get_object_or_404(Order, id=order_id, user=request.user)
        
        # Prevent double initialization if already successful
        if hasattr(order, 'payment') and order.payment.status == 'SUCCESS':
            return Response({"error": "Order already paid."}, status=status.HTTP_400_BAD_REQUEST)
            
        paystack = PaystackService()
        reference = f"PAY_{order.order_no}_{order.id.hex[:6]}"
        
        # Amount in NGN for service,Paystack handles kobo conversion
        init_data = paystack.initialize_transaction(
            email=request.user.email,
            amount=order.total_amount,
            reference=reference
        )
        
        if init_data and init_data.get('status'):
            # Create or update payment record
            Payment.objects.update_or_create(
                order=order,
                defaults={
                    'amount': order.total_amount,
                    'payment_method': payment_method,
                    'transaction_reference': reference,
                    'status': 'PENDING'
                }
            )
            return Response(init_data['data'], status=status.HTTP_200_OK)
            
        return Response({"error": "Payment initialization failed."}, status=status.HTTP_400_BAD_REQUEST)

class PaymentVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, reference):
        paystack = PaystackService()
        verify_data = paystack.verify_transaction(reference)
        
        if verify_data and verify_data.get('status') and verify_data['data']['status'] == 'success':
            payment = get_object_or_404(Payment, transaction_reference=reference)
            payment.status = 'SUCCESS'
            payment.gateway_response = verify_data['data']
            from django.utils import timezone
            payment.paid_at = timezone.now()
            payment.save()
            
            # Update order status if needed
            payment.order.status = 'CONFIRMED'
            payment.order.save()
            
            return Response({"status": "success", "message": "Payment verified."}, status=status.HTTP_200_OK)
            
        return Response({"status": "error", "message": "Payment verification failed."}, status=status.HTTP_400_BAD_REQUEST)
