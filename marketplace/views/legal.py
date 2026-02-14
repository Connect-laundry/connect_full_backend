from rest_framework import views, permissions, status, serializers
from rest_framework.response import Response
from ..models.legal import LegalDocument

class LegalDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalDocument
        fields = ['document_type', 'title', 'content', 'version', 'updated_at']

class LegalDocumentView(views.APIView):
    permission_classes = [permissions.AllowAny] # Public access
    
    def get(self, request, type=None):
        if type:
            # Get specific document
            try:
                doc = LegalDocument.objects.get(document_type=type.upper(), is_active=True)
                return Response({
                    "status": "success",
                    "data": LegalDocumentSerializer(doc).data
                })
            except LegalDocument.DoesNotExist:
                return Response({
                    "status": "error",
                    "message": "Document not found."
                }, status=status.HTTP_404_NOT_FOUND)
        else:
            # List all documents
            docs = LegalDocument.objects.filter(is_active=True)
            return Response({
                "status": "success",
                "data": LegalDocumentSerializer(docs, many=True).data
            })
