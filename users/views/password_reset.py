from rest_framework import status, response, views, permissions
from drf_spectacular.utils import extend_schema
from django.conf import settings
from django.utils import timezone
from ..models import User, PasswordResetToken
from ..serializers.password_reset import ForgotPasswordSerializer, ResetPasswordSerializer
from ..tasks import send_password_reset_email
from config.throttling import (
    PasswordResetAccountThrottle,
    PasswordResetIPThrottle,
    ResetPasswordIPThrottle,
)
from users.services.session_service import revoke_all_sessions_for_user

class ForgotPasswordView(views.APIView):
    """
    Endpoint to request a password reset email.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetIPThrottle, PasswordResetAccountThrottle]
    serializer_class = ForgotPasswordSerializer

    @extend_schema(request=ForgotPasswordSerializer)
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        user = User.objects.filter(email=email).first()

        if user:
            token_record, raw_token = PasswordResetToken.create_for_user(user)
            reset_link = f"{settings.FRONTEND_URL}/reset-password?resetId={token_record.id}"
            send_password_reset_email.delay(email, reset_link, raw_token)

        return response.Response({
            "message": "If an account exists with this email, you will receive a password reset link shortly."
        }, status=status.HTTP_200_OK)

class ResetPasswordView(views.APIView):
    """
    Endpoint to reset the password using the token.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ResetPasswordIPThrottle]
    serializer_class = ResetPasswordSerializer

    @extend_schema(request=ResetPasswordSerializer)
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_id = serializer.validated_data.get('reset_id')
        raw_token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        token_hash = PasswordResetToken._hash_token(raw_token)

        try:
            token_record = PasswordResetToken.objects.get(
                id=reset_id,
                token_hash=token_hash,
            ) if reset_id else PasswordResetToken.objects.get(token_hash=token_hash)
        except PasswordResetToken.DoesNotExist:
            return response.Response({
                "detail": "Invalid or expired token."
            }, status=status.HTTP_400_BAD_REQUEST)

        if not token_record.is_valid():
            return response.Response({
                "detail": "Invalid or expired token."
            }, status=status.HTTP_400_BAD_REQUEST)

        user = token_record.user
        user.set_password(new_password)
        user.save(update_fields=['password'])

        token_record.used_at = timezone.now()
        token_record.save(update_fields=['used_at'])
        revoke_all_sessions_for_user(user, reason='password_reset')

        return response.Response({
            "message": "Password successfully reset."
        }, status=status.HTTP_200_OK)
