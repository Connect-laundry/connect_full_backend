# pyre-ignore[missing-module]
from rest_framework import generics, permissions, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from .models import FAQ, Feedback
# pyre-ignore[missing-module]
from .serializers import FAQSerializer, FeedbackSerializer
# pyre-ignore[missing-module]
from config.throttling import FeedbackThrottle

class FAQView(generics.ListAPIView):
    """Retrieve FAQ or help documentation from the database."""
    queryset = FAQ.objects.filter(is_active=True)
    serializer_class = FAQSerializer
    permission_classes = [permissions.AllowAny]

class FeedbackView(generics.CreateAPIView):
    """Collect user feedback and persist it to the database."""
    queryset = Feedback.objects.all()
    serializer_class = FeedbackSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FeedbackThrottle]
