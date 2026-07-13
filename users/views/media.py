# pyre-ignore[missing-module]
from rest_framework import views, permissions, status, serializers
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.parsers import MultiPartParser, FormParser
# pyre-ignore[missing-module]
from django.conf import settings
from drf_spectacular.utils import extend_schema
from laundries.utils.validators import validate_file_upload
from utils.media import MediaStorageError, save_to_storage
import uuid
import os
import logging

logger = logging.getLogger(__name__)

ALLOWED_UPLOAD_FOLDERS = {'uploads', 'avatars'}


class MediaUploadSerializer(serializers.Serializer):
    file = serializers.ImageField()
    folder = serializers.ChoiceField(
        choices=sorted(ALLOWED_UPLOAD_FOLDERS),
        required=False,
        default='uploads',
    )

    def validate_file(self, value):
        request = self.context.get('request')
        if request and hasattr(request, 'request_id'):
            setattr(value, 'request_id', request.request_id)
        validate_file_upload(value)
        return value

class MediaUploadView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = 'burst_user'
    serializer_class = MediaUploadSerializer

    @extend_schema(request=MediaUploadSerializer)
    def post(self, request):
        # pyre-ignore[6]: Pyre doesn't understand DRF serializer initialization
        serializer = MediaUploadSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            uploaded_file = serializer.validated_data['file']
            folder = serializer.validated_data['folder']
            
            # Generate unique filename 
            ext = os.path.splitext(uploaded_file.name)[1]
            filename = f"{uuid.uuid4().hex}{ext}"
            file_path = f"{folder}/{filename}"
            
            # Save file — storage (Cloudinary/local) may be misconfigured or
            # unreachable; degrade to 503 rather than an unhandled 500. The
            # shared helper logs the failure with request/backend context.
            try:
                saved_path, file_url = save_to_storage(
                    file_path, uploaded_file, request=request, folder=folder,
                )
            except MediaStorageError:
                return Response(
                    {
                        "status": "error",
                        "message": "File storage is temporarily unavailable. Please try again later.",
                        "data": {},
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

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

        logger.info(
            "Media upload validation failed",
            extra={"request": request, "errors": serializer.errors},
        )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
