from rest_framework import views, permissions, status
from rest_framework.response import Response
from marketplace.models import FAQ

class FAQView(views.APIView):
    """Retrieve FAQ or help documentation from the database."""
    permission_classes = [permissions.AllowAny]
    
    def get(self, request):
        faqs = FAQ.objects.filter(is_active=True)
        data = [{"question": f.question, "answer": f.answer} for f in faqs]
        return Response({
            "status": "success",
            "message": "FAQs fetched",
            "data": data
        })
