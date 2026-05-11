# pyre-ignore[missing-module]
# pyre-ignore[missing-module]
from rest_framework import generics, permissions, viewsets, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from ..models import Address
# pyre-ignore[missing-module]
from ..serializers.profile import ProfileSerializer, AddressSerializer
from users.serializers.session import RefreshTokenRequestSerializer
from users.services.session_service import revoke_current_session, revoke_all_sessions_for_user

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
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = RefreshTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revoke_current_session(
            request.user,
            submitted_refresh=serializer.validated_data['refresh'],
            request=request,
            reason='logout',
        )
        return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)


class DeleteAccountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        user = request.user
        reason = request.data.get('reason') or 'self_service_deletion'
        revoke_all_sessions_for_user(user, reason='account_deleted')

        tombstone_suffix = str(user.id).replace('-', '')[:12]
        user.first_name = ''
        user.last_name = ''
        user.avatar = None
        user.email = f'deleted-{tombstone_suffix}@deleted.connect'
        user.phone = f'deleted-{tombstone_suffix}'
        user.is_active = False
        user.deactivated_at = timezone.now()
        user.deactivation_reason = reason
        user.set_unusable_password()
        user.save(update_fields=[
            'first_name',
            'last_name',
            'avatar',
            'email',
            'phone',
            'is_active',
            'deactivated_at',
            'deactivation_reason',
            'password',
            'updated_at',
        ])
        request.user.addresses.all().delete()

        return Response({
            "status": "success",
            "message": "Account deleted successfully.",
            "data": {
                "deleted": True,
                "deactivated_at": user.deactivated_at,
            }
        }, status=status.HTTP_200_OK)

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
