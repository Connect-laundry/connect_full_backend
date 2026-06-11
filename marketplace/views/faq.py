from rest_framework import views, permissions, serializers
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from marketplace.models import FAQ


class FAQItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    question = serializers.CharField()
    answer = serializers.CharField()
    order = serializers.IntegerField()


class FAQResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = FAQItemSerializer(many=True)


class FAQView(views.APIView):
    """Retrieve FAQ or help documentation from the database."""
    permission_classes = [permissions.AllowAny]
    
    @extend_schema(responses=FAQResponseSerializer)
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
        return Response({
            "status": "success",
            "message": "FAQs retrieved successfully.",
            "data": data
        })
