# pyre-ignore[missing-module]
# pyre-ignore[missing-module]
from rest_framework import generics, permissions, viewsets, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from ..models import Address
# pyre-ignore[missing-module]
from ..serializers.profile import ProfileSerializer, AddressSerializer

class ProfileView(generics.RetrieveUpdateAPIView):
    """GET and PATCH for the currently authenticated user's profile."""
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({
            "user": serializer.data
        })

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({
            "user": serializer.data
        })

class AddressViewSet(viewsets.ModelViewSet):
    """CRUD for user addresses."""
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

class LogoutView(APIView):
    """
    Blacklist the refresh token to log out the user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            from rest_framework_simplejwt.tokens import RefreshToken
            refresh_token = request.data.get("refreshToken") or request.data.get("refresh")
            if not refresh_token:
                return Response({"detail": "Refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)
                
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class SupportedCitiesView(APIView):
    """Returns a list of unique cities where laundries are available."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # pyre-ignore[missing-module]
        from laundries.models.laundry import Laundry
        cities = Laundry.objects.filter(is_active=True, status='APPROVED').values_list('city', flat=True).distinct()
        return Response({
            "status": "success",
            "cities": list(cities)
        })
