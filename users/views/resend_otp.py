# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework import status, permissions
# pyre-ignore[missing-module]
from ..services.otp_service import OTPService
# pyre-ignore[missing-module]
from ..tasks import send_otp_email, send_otp_sms
# pyre-ignore[missing-module]
from ..models import User

class ResendOTPView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        identifier = request.data.get('identifier')
        if not identifier:
            return Response({
                "status": "error",
                "message": "Identifier (email or phone) is required."
            }, status=status.HTTP_400_BAD_REQUEST)

        otp_service = OTPService()
        if otp_service.check_resend_lock(identifier):
            return Response({
                "status": "error",
                "message": "Please wait before requesting a new OTP."
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)

        user = User.objects.filter(email=identifier).first() or \
               User.objects.filter(phone=identifier).first()
        
        if not user:
            return Response({
                "status": "error",
                "message": "User not found."
            }, status=status.HTTP_404_NOT_FOUND)

        otp = otp_service.generate_otp()
        otp_service.save_otp(identifier, otp)
        
        # Trigger async task based on identifier type
        if '@' in identifier:
            send_otp_email.delay(identifier, otp)
        else:
            send_otp_sms.delay(identifier, otp)

        otp_service.set_resend_lock(identifier)

        return Response({
            "status": "success",
            "message": "OTP has been resent."
        }, status=status.HTTP_200_OK)
