# pyre-ignore[missing-module]
from rest_framework import views, permissions, status, serializers
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from ..models import User

class ReferralApplySerializer(serializers.Serializer):
    referral_code = serializers.CharField(max_length=20)

class ReferralApplyView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    
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
                
            # Prevent self-referral
            if referrer == request.user:
                return Response(
                    {"status": "error", "message": "You cannot refer yourself."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Apply referral
            with transaction.atomic():
                request.user.referred_by = referrer
                request.user.save()
                
            return Response({
                "status": "success",
                "message": f"Referral code applied. You were referred by {referrer.get_full_name() or referrer.email}."
            })
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ReferralStatsView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    
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
