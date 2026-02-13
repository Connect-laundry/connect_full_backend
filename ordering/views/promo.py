from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db import transaction
from django.utils import timezone
from ordering.models.promo import Coupon
from ordering.serializers.promo import CouponValidateSerializer, CouponResponseSerializer

class CouponViewSet(viewsets.GenericViewSet):
    """
    Endpoints for coupon validation and management.
    """
    queryset = Coupon.objects.filter(is_active=True)
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='validate')
    def validate_coupon(self, request):
        """
        Validates a coupon code against order details.
        Uses select_for_update() for concurrency safety.
        """
        serializer = CouponValidateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        code = serializer.validated_data['code']
        order_amount = serializer.validated_data['order_amount']
        laundry_id = serializer.validated_data.get('laundry_id')

        try:
            with transaction.atomic():
                # select_for_update prevents usage_limit race conditions
                coupon = Coupon.objects.select_for_update().get(
                    code__iexact=code,
                    is_active=True
                )
                
                is_valid, message, discount = coupon.validate_for_order(
                    order_amount=order_amount,
                    user=request.user,
                    laundry_id=laundry_id
                )

                if not is_valid:
                    return Response({
                        "status": "error",
                        "message": message,
                        "data": {}
                    }, status=status.HTTP_400_BAD_REQUEST)

                return Response({
                    "status": "success",
                    "message": "Coupon valid",
                    "data": {
                        "discount_amount": str(discount),
                        "final_amount": str(order_amount - discount),
                        "discount_type": coupon.discount_type,
                        "expires_at": coupon.expires_at
                    }
                })

        except Coupon.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Invalid coupon code",
                "data": {}
            }, status=status.HTTP_400_BAD_REQUEST)
