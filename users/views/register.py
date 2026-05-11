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

        user = serializer.save()
        service = AuthService()
        tokens = service.get_tokens_for_user(user, request)

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
