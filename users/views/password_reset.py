from rest_framework import status, response, views, permissions
from django.conf import settings
from django.utils import timezone
from ..models import User, PasswordResetToken
from ..serializers.password_reset import ForgotPasswordSerializer, ResetPasswordSerializer
from ..tasks import send_password_reset_email
from config.throttling import PasswordResetThrottle

class ForgotPasswordView(views.APIView):
    """
    Endpoint to request a password reset email.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            user = User.objects.filter(email=email).first()
            
            # Silent failure if user doesn't exist (enumeration protection)
            if user:
                raw_token = PasswordResetToken.create_for_user(user)
                reset_link = f"{settings.FRONTEND_URL}/reset-password?token={raw_token}"
                
                # Send email via Celery task
                send_password_reset_email.delay(email, reset_link)
            
            return response.Response({
                "message": "If an account exists with this email, you will receive a password reset link shortly."
            }, status=status.HTTP_200_OK)
            
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ResetPasswordView(views.APIView):
    """
    Endpoint to reset the password using the token.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        if serializer.is_valid():
            raw_token = serializer.validated_data['token']
            new_password = serializer.validated_data['new_password']
            
            token_hash = PasswordResetToken._hash_token(raw_token)
            
            try:
                token_record = PasswordResetToken.objects.get(
                    token_hash=token_hash
                )
            except PasswordResetToken.DoesNotExist:
                return response.Response({
                    "detail": "Invalid or expired token."
                }, status=status.HTTP_400_BAD_REQUEST)
                
            if not token_record.is_valid():
                return response.Response({
                    "detail": "Invalid or expired token."
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Reset password
            user = token_record.user
            user.set_password(new_password)
            user.save()
            
            # Mark token as used
            token_record.used_at = timezone.now()
            token_record.save()
            
            return response.Response({
                "message": "Password successfully reset."
            }, status=status.HTTP_200_OK)
            
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
