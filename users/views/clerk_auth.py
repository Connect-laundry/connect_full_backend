from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from ..serializers.profile import ProfileSerializer
from ..auth.clerk import ClerkAuthentication

class VerifyClerkTokenView(APIView):
    """
    Exchange a Clerk JWT for backend SimpleJWT tokens.
    Authentication is handled by ClerkAuthentication class.
    """
    authentication_classes = [ClerkAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'auth'

    def post(self, request):
        user = request.user
        refresh = RefreshToken.for_user(user)
        
        return Response({
            'status': 'success',
            'message': 'Token verified successfully',
            'data': {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': ProfileSerializer(user).data
            }
        }, status=status.HTTP_200_OK)

class ClerkMeView(APIView):
    """
    Returns the current user profile.
    Accepts both Clerk tokens and Backend tokens.
    """
    # Note: In production, you might want to only accept backend tokens once issued,
    # or keep ClerkAuthentication as a secondary for flexibility.
    # For now, we allow the verification flow to be decoupled.
    
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({
            'status': 'success',
            'message': 'Profile retrieved successfully',
            'data': ProfileSerializer(request.user).data
        }, status=status.HTTP_200_OK)

class ClerkLogoutView(APIView):
    """
    Logout view to blacklist tokens if using SimpleJWT.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({
                'status': 'success',
                'message': 'Logged out successfully',
                'data': {}
            }, status=status.HTTP_200_OK)
        except Exception:
            return Response({
                'status': 'error',
                'message': 'Invalid token',
                'data': {}
            }, status=status.HTTP_400_BAD_MESSAGE)
