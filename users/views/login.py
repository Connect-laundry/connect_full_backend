# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.permissions import AllowAny
# pyre-ignore[missing-module]
from ..serializers.login import LoginSerializer
# pyre-ignore[missing-module]
from ..services.auth_service import AuthService

class LoginView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            service = AuthService()
            try:
                user, tokens = service.login_user(
                    email=serializer.validated_data['email'],
                    password=serializer.validated_data['password']
                )
                return Response({
                    "user_id": user.id,
                    "email": user.email,
                    **tokens
                }, status=status.HTTP_200_OK)
            except Exception as e:
                return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
