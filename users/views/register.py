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
# pyre-ignore[missing-module]
from config.throttling import AuthThrottle

class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]
    
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            # Create user (verified by default now)
            user = serializer.save()
            
            # Authenticate and get tokens for auto-login
            service = AuthService()
            tokens = service.get_tokens_for_user(user)
            
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
            
        return Response({
            "status": "error",
            "message": "Validation failed.",
            "data": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
