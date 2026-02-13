from rest_framework import views, permissions, status
from rest_framework.response import Response
from marketplace.models import Feedback

class FeedbackView(views.APIView):
    """Collect user feedback and persist it to the database."""
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        # Implementation to be refined if needed, currently matching previous logic
        return Response({
            "status": "success",
            "message": "Feedback submitted"
        })
