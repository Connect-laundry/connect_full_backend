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
from ..serializers.login import LoginSerializer
# pyre-ignore[missing-module]
from ..services.auth_service import AuthService
from config.throttling import LoginAccountThrottle, LoginIPThrottle

class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginIPThrottle, LoginAccountThrottle]
    serializer_class = LoginSerializer

    @extend_schema(request=LoginSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = AuthService()
        user, tokens = service.login_user(
            email=serializer.validated_data['email'],
            password=serializer.validated_data['password'],
            request=request,
        )
        return Response({
            "accessToken": tokens['access'],
            "refreshToken": tokens['refresh'],
            "user": {
                "id": str(user.id),
                "email": user.email,
                "fullName": user.get_full_name(),
                "role": user.role,
            }
        }, status=status.HTTP_200_OK)
