from rest_framework import status, response, views, permissions
from drf_spectacular.utils import extend_schema
from ..models import User, PasswordResetToken
from ..serializers.password_reset import ForgotPasswordSerializer, ResetPasswordSerializer
from ..tasks import send_password_reset_email
from utils.tasks import safe_task_delay
from config.throttling import PASSWORD_RESET_THROTTLES, RESET_PASSWORD_THROTTLES
from users.services.password_reset import apply_password_reset, build_reset_link, find_valid_token

class ForgotPasswordView(views.APIView):
    """
    Endpoint to request a password reset email.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = PASSWORD_RESET_THROTTLES
    serializer_class = ForgotPasswordSerializer

    @extend_schema(request=ForgotPasswordSerializer)
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        # Emails are stored as typed at sign-up; match case-insensitively.
        user = User.objects.filter(email__iexact=email.strip()).first()

        if user:
            token_record, raw_token = PasswordResetToken.create_for_user(user)
            reset_link = build_reset_link(request, token_record)
            # Broker outage must not break password reset — send inline if
            # the queue is unavailable.
            safe_task_delay(
                send_password_reset_email, user.email, reset_link, raw_token,
                fallback_sync=True,
            )

        return response.Response({
            "message": "If an account exists with this email, you will receive a password reset link shortly."
        }, status=status.HTTP_200_OK)

class ResetPasswordView(views.APIView):
    """
    Endpoint to reset the password using the token.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = RESET_PASSWORD_THROTTLES
    serializer_class = ResetPasswordSerializer

    @extend_schema(request=ResetPasswordSerializer)
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_id = serializer.validated_data.get('reset_id')
        raw_token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        token_record = find_valid_token(reset_id, raw_token)
        if token_record is None:
            return response.Response({
                "detail": "Invalid or expired token."
            }, status=status.HTTP_400_BAD_REQUEST)

        apply_password_reset(token_record, new_password)

        return response.Response({
            "message": "Password successfully reset."
        }, status=status.HTTP_200_OK)
