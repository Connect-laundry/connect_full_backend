from rest_framework import views, permissions, status, serializers
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.shortcuts import get_object_or_404
# pyre-ignore[missing-module]
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
# pyre-ignore[missing-module]
from ..models import User
from users.services.session_service import revoke_all_sessions_for_user

class UserDeactivateView(views.APIView):
    """
    API endpoint for admins to deactivate a user account (Soft-Delete).
    """
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    @extend_schema(
        request=None,
        responses={200: inline_serializer(name='DeactivateResponse', fields={'status': serializers.CharField(), 'message': serializers.CharField(), 'data': serializers.JSONField()})}
    )
    def patch(self, request, pk=None):
        user = get_object_or_404(User, pk=pk)
        reason = request.data.get('reason', 'No reason provided')
        
        if not user.is_active:
            return Response(
                {"status": "error", "message": "User is already inactive"},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.is_active = False
        user.deactivated_at = timezone.now()
        user.deactivation_reason = reason
        user.save()
        revoke_all_sessions_for_user(user, reason='account_deactivated')
        
        return Response({
            "status": "success",
            "message": f"User {user.email} has been deactivated",
            "data": {
                "id": user.id,
                "email": user.email,
                "deactivated_at": user.deactivated_at,
                "reason": user.deactivation_reason
            }
        })
