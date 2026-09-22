# pyre-ignore[missing-module]
from config.throttling import GeneralThrottle, ReferralApplyThrottle
from rest_framework import views, permissions, status, serializers
from drf_spectacular.utils import extend_schema, inline_serializer
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.conf import settings
from django.db import transaction
# pyre-ignore[missing-module]
from ..models import User

class ReferralApplySerializer(serializers.Serializer):
    referral_code = serializers.CharField(max_length=20)

class ReferralApplyView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [GeneralThrottle, ReferralApplyThrottle]
    serializer_class = ReferralApplySerializer
    
    @extend_schema(request=ReferralApplySerializer)
    def post(self, request):
        # pyre-ignore
        serializer = ReferralApplySerializer(data=request.data)
        if serializer.is_valid():
            code = serializer.validated_data['referral_code']
            
            # Check if user already has a referrer
            if request.user.referred_by:
                return Response(
                    {"status": "error", "message": "You have already been referred."},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            # Find referrer
            try:
                referrer = User.objects.get(referral_code=code)
            except User.DoesNotExist:
                return Response(
                    {"status": "error", "message": "Invalid referral code."},
                    status=status.HTTP_404_NOT_FOUND
                )
                
            # Prevent self-referral and referral loops (A->B then B->A).
            if referrer == request.user or referrer.referred_by_id == request.user.id:
                return Response(
                    {"status": "error", "message": "This referral code can't be used on your account."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            with transaction.atomic():
                # Lock both endpoints in a consistent order, then re-check.
                # Otherwise reciprocal concurrent requests can both pass above.
                participants = {u.pk: u for u in User.objects.select_for_update()
                                .filter(pk__in=[request.user.pk, referrer.pk]).order_by('pk')}
                locked = participants[request.user.pk]
                referrer = participants[referrer.pk]
                if getattr(settings, 'PROMO_REQUIRE_VERIFIED_EMAIL', False) and not locked.email_verified:
                    return Response(
                        {"status": "error", "message": "Verify your email before applying a referral code."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if referrer.pk == locked.pk or referrer.referred_by_id == locked.pk:
                    return Response(
                        {"status": "error", "message": "This referral code can't be used on your account."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if locked.referred_by_id:
                    return Response(
                        {"status": "error", "message": "You have already been referred."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                locked.referred_by = referrer
                locked.save(update_fields=['referred_by'])

            # Never echo the referrer's name or email: combined with code
            # guessing it would let anyone harvest other customers' identities.
            return Response({
                "status": "success",
                "message": "Referral code applied."
            })
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ReferralStatsView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    @extend_schema(
        request=None,
        responses={200: inline_serializer(name='ReferralStatsResponse', fields={'status': serializers.CharField(), 'data': serializers.JSONField()})}
    )
    def get(self, request):
        referrals = request.user.referrals.count()
        # Potential future expansion: Earnings from referrals
        
        return Response({
            "status": "success",
            "data": {
                "referral_code": request.user.referral_code,
                "total_referrals": referrals,
                "earnings": "0.00" # Placeholder for future logic
            }
        })
