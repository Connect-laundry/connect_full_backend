from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.conf import settings
from ..services.auth_service import AuthService

User = get_user_model()

class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            user = User.objects.get(email=email)
            # Generate OTP for password reset (reusing existing OTP logic)
            auth_service = AuthService()
            otp = auth_service.generate_otp(email)
            
            # Send Email
            send_mail(
                'Password Reset OTP',
                f'Your password reset OTP is: {otp}. It expires in 5 minutes.',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            
            return Response({"message": "OTP sent to your email."}, status=status.HTTP_200_OK)
        except User.DoesNotExist:
            # We return 200 even if user doesn't exist for security (avoid enumeration)
            return Response({"message": "If this email exists, an OTP has been sent."}, status=status.HTTP_200_OK)

class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        otp = request.data.get('otp')
        new_password = request.data.get('new_password')
        
        if not all([email, otp, new_password]):
            return Response({"error": "All fields are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        auth_service = AuthService()
        if auth_service.verify_otp(email, otp):
            try:
                user = User.objects.get(email=email)
                user.set_password(new_password)
                user.save()
                return Response({"message": "Password reset successful."}, status=status.HTTP_200_OK)
            except User.DoesNotExist:
                return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        
        return Response({"error": "Invalid or expired OTP."}, status=status.HTTP_400_BAD_REQUEST)
