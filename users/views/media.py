# pyre-ignore[missing-module]
from rest_framework import views, permissions, status, serializers
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.parsers import MultiPartParser, FormParser
# pyre-ignore[missing-module]
from django.core.files.storage import default_storage
# pyre-ignore[missing-module]
from django.conf import settings
import uuid
import os

class MediaUploadSerializer(serializers.Serializer):
    file = serializers.ImageField()
    folder = serializers.CharField(required=False, default='uploads')

class MediaUploadView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = 'burst_user'

    def post(self, request):
        # pyre-ignore[6]: Pyre doesn't understand DRF serializer initialization
        serializer = MediaUploadSerializer(data=request.data)
        if serializer.is_valid():
            uploaded_file = serializer.validated_data['file']
            folder = serializer.validated_data['folder']
            
            # Generate unique filename
            ext = os.path.splitext(uploaded_file.name)[1]
            filename = f"{uuid.uuid4().hex}{ext}"
            file_path = f"{folder}/{filename}"
            
            # Save file
            saved_path = default_storage.save(file_path, uploaded_file)
            file_url = default_storage.url(saved_path)
            
            # Ensure full URL if needed (though default_storage.url usually gives relative or absolute depending on config)
            if not file_url.startswith('http'):
                file_url = request.build_absolute_uri(file_url)

            return Response({
                "status": "success",
                "message": "File uploaded successfully",
                "data": {
                    "url": file_url,
                    "filename": filename,
                    "type": uploaded_file.content_type
                }
            })
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
