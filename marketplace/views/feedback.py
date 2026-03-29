from rest_framework import views, permissions, status
from rest_framework.response import Response
from marketplace.models import Feedback
from marketplace.serializers import FeedbackSerializer

class FeedbackView(views.APIView):
    """Collect user feedback and persist it to the database."""
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        serializer = FeedbackSerializer(data=request.data)
        if serializer.is_valid():
            # Automatically attach the authenticated user
            serializer.save(user=request.user)
            return Response({
                "success": True,
                "message": "Feedback submitted successfully.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)
            
        return Response({
            "success": False,
            "status": "error",
            "message": "Validation failed.",
            "data": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
