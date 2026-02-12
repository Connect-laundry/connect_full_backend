# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.permissions import AllowAny
# pyre-ignore[missing-module]
from ..serializers.register import RegisterSerializer
# pyre-ignore[missing-module]
from ..services.auth_service import AuthService

class RegisterView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            service = AuthService()
            user, tokens = service.register_user(serializer.validated_data)
            return Response({
                "user_id": user.id,
                "email": user.email,
                **tokens
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
