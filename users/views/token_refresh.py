# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework import permissions, status
# pyre-ignore[missing-module]
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from config.throttling import RefreshIPThrottle
from users.serializers.session import RefreshTokenRequestSerializer
from users.services.session_service import rotate_refresh_token


class CustomTokenRefreshView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RefreshIPThrottle]
    serializer_class = RefreshTokenRequestSerializer

    @extend_schema(request=RefreshTokenRequestSerializer)
    def post(self, request, *args, **kwargs):
        serializer = RefreshTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = rotate_refresh_token(serializer.validated_data['refresh'], request)

        return Response({
            "accessToken": tokens['access'],
            "refreshToken": tokens['refresh'],
        }, status=status.HTTP_200_OK)
