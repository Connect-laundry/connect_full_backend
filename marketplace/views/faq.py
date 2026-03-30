from rest_framework import views, permissions, status
from rest_framework.response import Response
from marketplace.models import FAQ


class FAQView(views.APIView):
    """Retrieve FAQ or help documentation from the database."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        faqs = FAQ.objects.filter(is_active=True)
        data = [
            {
                "id": str(f.id),
                "question": f.question,
                "answer": f.answer,
                "order": f.order,
            }
            for f in faqs
        ]
        return Response(
            {
                "status": "success",
                "message": "FAQs retrieved successfully.",
                "data": data,
            }
        )
