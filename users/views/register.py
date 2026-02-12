# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.permissions import AllowAny
# pyre-ignore[missing-module]
from ..serializers.register import RegisterSerializer
# pyre-ignore[missing-module]
from ..services.otp_service import OTPService
# pyre-ignore[missing-module]
from ..tasks import send_otp_email, send_otp_sms

class RegisterView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            # Create user (unverified)
            user = serializer.save()
            
            # Generate and save OTP
            otp_service = OTPService()
            otp = otp_service.generate_otp()
            
            # Use email for primary OTP delivery
            identifier = user.email
            otp_service.save_otp(identifier, otp)
            
            # Trigger async tasks
            send_otp_email.delay(user.email, otp)
            if user.phone:
                send_otp_sms.delay(user.phone, otp)
            
            return Response({
                "status": "success",
                "message": "User registered successfully. Please verify your email/phone with the OTP sent.",
                "data": {
                    "user_id": user.id,
                    "email": user.email
                }
            }, status=status.HTTP_201_CREATED)
            
        return Response({
            "status": "error",
            "message": "Validation failed.",
            "data": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
