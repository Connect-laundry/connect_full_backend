from rest_framework import views, permissions, status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ..models import User

class UserDeactivateView(views.APIView):
    """
    API endpoint for admins to deactivate a user account (Soft-Delete).
    """
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

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

        # Revoke tokens (optional logic depending on JWT blacklist setup)
        
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
