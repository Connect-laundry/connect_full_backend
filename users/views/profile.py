# pyre-ignore[missing-module]
from rest_framework import generics, permissions, viewsets, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
from ..models import Address
from ..serializers.profile import ProfileSerializer, AddressSerializer

class ProfileView(generics.RetrieveUpdateAPIView):
    """GET and PATCH for the currently authenticated user's profile."""
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

class AddressViewSet(viewsets.ModelViewSet):
    """CRUD for user addresses."""
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

class LogoutView(APIView):
    """
    Simulated logout. 
    In JWT setup, typicallyhandled on client side by deleting the token.
    This endpoint can be used to blacklist tokens if using SimpleJWT's blacklist app.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
