# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema
# pyre-ignore[missing-module]
from ..serializers.register import RegisterSerializer
# pyre-ignore[missing-module]
from ..services.auth_service import AuthService
# pyre-ignore[missing-module]
from config.throttling import RegisterAccountThrottle, RegisterIPThrottle

class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RegisterIPThrottle, RegisterAccountThrottle]
    serializer_class = RegisterSerializer

    @extend_schema(request=RegisterSerializer)
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Atomic registration: the user row and ALL session/token side-effects
        # (DeviceSession, SessionRefreshToken, audit) must commit together or
        # roll back together. Otherwise a failure after user creation leaves an
        # orphaned account whose email/phone are permanently reserved.
        with transaction.atomic():
            user = serializer.save()
            service = AuthService()
            tokens = service.get_tokens_for_user(user, request)
            self._record_registration_audit(user, request)

        return Response({
            "accessToken": tokens['access'],
            "refreshToken": tokens['refresh'],
            "user": {
                "id": str(user.id),
                "email": user.email,
                "fullName": user.get_full_name(),
                "role": user.role
            }
        }, status=status.HTTP_201_CREATED)

    @staticmethod
    def _record_registration_audit(user, request):
        """Audit the successful self-registration.

        Best-effort by design (``record_audit`` swallows its own errors), but it
        runs inside the registration transaction so the audit row rolls back if a
        later step in the same request fails.
        """
        try:
            from marketplace.services.audit import record_audit
            from marketplace.models import AuditLog
            record_audit(
                action=AuditLog.Action.SECURITY_EVENT,
                actor=user,
                request=request,
                target_type='User',
                target_id=user.id,
                target_repr=user.email,
                metadata={'event': 'user_registered', 'role': user.role},
            )
        except Exception:  # pragma: no cover - defensive
            pass
