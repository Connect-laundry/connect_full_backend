# pyre-ignore[missing-module]
from rest_framework import permissions, status, serializers
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, inline_serializer

from users.models import DeviceSession
from users.serializers.session import DeviceSessionSerializer, RefreshTokenRequestSerializer
from users.services.session_service import (
    get_request_session_id,
    revoke_all_sessions_for_user,
    revoke_current_session,
)


class ActiveSessionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DeviceSessionSerializer

    @extend_schema(request=None, responses=DeviceSessionSerializer(many=True))
    def get(self, request):
        sessions = DeviceSession.objects.filter(
            user=request.user,
            revoked_at__isnull=True,
        ).order_by('-last_used_at')
        serializer = DeviceSessionSerializer(
            sessions,
            many=True,
            context={'current_session_id': get_request_session_id(request)},
        )
        return Response({'sessions': serializer.data}, status=status.HTTP_200_OK)


class RevokeCurrentSessionView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RefreshTokenRequestSerializer

    @extend_schema(request=RefreshTokenRequestSerializer)
    def post(self, request):
        serializer = RefreshTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revoke_current_session(
            request.user,
            submitted_refresh=serializer.validated_data['refresh'],
            request=request,
            reason='session_revoked',
        )
        return Response({'detail': 'Current session revoked.'}, status=status.HTTP_200_OK)


class RevokeAllSessionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=None,
        responses={200: inline_serializer(name='RevokeAllResponse', fields={'detail': serializers.CharField()})}
    )
    def post(self, request):
        revoke_all_sessions_for_user(request.user, reason='logout_all')
        return Response({'detail': 'All sessions revoked.'}, status=status.HTTP_200_OK)
