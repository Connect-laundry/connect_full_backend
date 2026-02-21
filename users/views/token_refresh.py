# pyre-ignore[missing-module]
from rest_framework_simplejwt.views import TokenRefreshView
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework import status

class CustomTokenRefreshView(TokenRefreshView):
    """
    Custom Refresh View to return 'accessToken' instead of 'access' 
    to match our project's standardized auth response format.
    """
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)

        return Response({
            "accessToken": serializer.validated_data.get('access'),
        }, status=status.HTTP_200_OK)
