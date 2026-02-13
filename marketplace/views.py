# pyre-ignore[missing-module]
from rest_framework import views, permissions, status
# pyre-ignore[missing-module]
from rest_framework.response import Response

class FeedbackView(views.APIView):
    """Simple endpoint to collect user feedback."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # In a real app, save to a Feedback model
        return Response({"detail": "Feedback received. Thank you!"}, status=status.HTTP_201_CREATED)

class FAQView(views.APIView):
    """Retrieve FAQ or help documentation (Mocked)."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        faqs = [
            {"q": "How long does it take?", "a": "Standard delivery is 24-48 hours."},
            {"q": "How do I pay?", "a": "We accept Mobile Money and Cards."}
        ]
        return Response(faqs)
