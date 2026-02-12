# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework import status, permissions
# pyre-ignore[missing-module]
from ..serializers.verify_otp import VerifyOTPSerializer
# pyre-ignore[missing-module]
from ..services.otp_service import OTPService
# pyre-ignore[missing-module]
from ..models import User

class VerifyOTPView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        if serializer.is_valid():
            identifier = serializer.validated_data['identifier']
            otp = serializer.validated_data['otp']
            
            otp_service = OTPService()
            success, message = otp_service.validate_otp(identifier, otp)
            
            if success:
                # Mark user as verified
                try:
                    # User might have registered by email or phone
                    user = User.objects.filter(email=identifier).first() or \
                           User.objects.filter(phone=identifier).first()
                    
                    if user:
                        user.is_verified = True
                        user.save()
                        return Response({
                            "status": "success",
                            "message": "User verified successfully."
                        }, status=status.HTTP_200_OK)
                    return Response({
                        "status": "error",
                        "message": "User not found."
                    }, status=status.HTTP_404_NOT_FOUND)
                except Exception as e:
                    return Response({
                        "status": "error",
                        "message": str(e)
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            return Response({
                "status": "error",
                "message": message
            }, status=status.HTTP_400_BAD_REQUEST)
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
