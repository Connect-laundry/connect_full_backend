import logging
import uuid
from rest_framework import generics, permissions, viewsets, status, serializers
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter, inline_serializer
# pyre-ignore[missing-module]
from ..models import Address
# pyre-ignore[missing-module]
from ..serializers.profile import ProfileSerializer, AddressSerializer
from users.serializers.session import RefreshTokenRequestSerializer
from users.services.account_deletion import anonymize_local_account
from users.services.apple_revocation import revoke_apple_for_account_deletion
from users.services.clerk_service import delete_clerk_user
from users.services.session_service import revoke_current_session

def _request_event_suffix(request):
    return (
        request.META.get('HTTP_X_IDEMPOTENCY_KEY')
        or request.META.get('HTTP_X_REQUEST_ID')
        or str(uuid.uuid4())
    )


logger = logging.getLogger(__name__)


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
        from marketplace.services.customer_events import notify_customer_event
        notify_customer_event(
            request.user,
            'PROFILE_UPDATED',
            dedup_key=f'profile_updated:{request.user.id}:{_request_event_suffix(request)}',
        )
        return Response({
            "user": serializer.data
        })


@extend_schema_view(
    retrieve=extend_schema(parameters=[OpenApiParameter("id", type=uuid.UUID, location=OpenApiParameter.PATH)]),
    update=extend_schema(parameters=[OpenApiParameter("id", type=uuid.UUID, location=OpenApiParameter.PATH)]),
    partial_update=extend_schema(parameters=[OpenApiParameter("id", type=uuid.UUID, location=OpenApiParameter.PATH)]),
    destroy=extend_schema(parameters=[OpenApiParameter("id", type=uuid.UUID, location=OpenApiParameter.PATH)]),
)
class AddressViewSet(viewsets.ModelViewSet):
    """CRUD for user addresses."""
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def _notify_address_event(self, event, instance):
        from marketplace.services.customer_events import notify_customer_event
        notify_customer_event(
            self.request.user,
            event,
            dedup_key=f'address:{event}:{instance.id}:{_request_event_suffix(self.request)}',
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        self._notify_address_event('ADDRESS_SAVED', instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        self._notify_address_event('ADDRESS_UPDATED', instance)

    def perform_destroy(self, instance):
        address_id = instance.id
        instance.delete()
        from marketplace.services.customer_events import notify_customer_event
        notify_customer_event(
            self.request.user,
            'ADDRESS_REMOVED',
            dedup_key=f'address:ADDRESS_REMOVED:{address_id}:{_request_event_suffix(self.request)}',
        )


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RefreshTokenRequestSerializer

    @extend_schema(request=RefreshTokenRequestSerializer)
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

    @extend_schema(
        request=None,
        responses={200: inline_serializer(name='DeleteAccountResponse', fields={'status': serializers.CharField(), 'message': serializers.CharField(), 'data': serializers.JSONField()})}
    )
    def delete(self, request):
        user = request.user
        reason = request.data.get('reason') or 'self_service_deletion'
        apple_code = request.data.get('apple_authorization_code')
        apple_code = apple_code if isinstance(apple_code, str) else None
        apple_revocation = 'not_applicable'
        if user.clerk_user_id:
            # Revoke before deleting the Clerk user: the Apple subject lookup needs it.
            outcome = revoke_apple_for_account_deletion(user.clerk_user_id, apple_code)
            logger.info(
                'Account deletion Apple revocation outcome: %s',
                outcome,
                extra={'user_id': str(user.pk), 'apple_revocation': outcome},
            )
            # Lets the app show TN3194's manual "stop using Apple" steps when needed.
            # Only the code-exchange path is verified: Apple answers /auth/revoke with
            # 200 even for a bad client secret, so the Clerk-token path cannot prove
            # revocation and the app still shows the manual steps for it.
            apple_revocation = 'revoked' if outcome == 'revoked' else 'not_revoked'
            delete_clerk_user(user.clerk_user_id)
        result = anonymize_local_account(user, reason=reason, request=request)
        return Response({
            "status": "success",
            "message": "Account deleted successfully.",
            "data": {
                "deleted": True,
                "deactivated_at": result.deactivated_at,
                "apple_revocation": apple_revocation,
            }
        }, status=status.HTTP_200_OK)

class SupportedCitiesView(APIView):
    """Returns a list of unique cities where laundries are available."""
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        request=None,
        responses={200: inline_serializer(name='SupportedCitiesResponse', fields={'status': serializers.CharField(), 'cities': serializers.ListField(child=serializers.CharField())})}
    )
    def get(self, request):
        # pyre-ignore[missing-module]
        from laundries.models.laundry import Laundry
        cities = Laundry.objects.filter(is_active=True, status='APPROVED').values_list('city', flat=True).distinct()
        return Response({
            "status": "success",
            "cities": list(cities)
        })
