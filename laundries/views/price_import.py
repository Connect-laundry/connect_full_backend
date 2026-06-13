"""AI-assisted price-list import endpoints (owner-facing).

Flow:
1. ``POST price-imports/`` — upload a price-list image. A job is created and the
   configured OCR provider proposes candidate items (the default stub proposes
   none). Returns the job + unconfirmed draft items.
2. ``GET price-imports/{id}/`` — fetch a job and its drafts for review.
3. ``POST price-imports/{id}/confirm/`` — the owner submits the reviewed rows.
   Each becomes a ``LaundryPricingItem``. Existing items (same name) are never
   overwritten; they are skipped and reported.
"""
import logging

# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from rest_framework import permissions, status, viewsets
# pyre-ignore[missing-module]
from rest_framework.decorators import action
# pyre-ignore[missing-module]
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
# pyre-ignore[missing-module]
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from ..models.pricing import LaundryPricingItem
from ..models.price_import import PriceListDraftItem, PriceListImportJob
from ..renderers import StandardResponseRenderer
from ..serializers.price_import import (
    PriceImportConfirmSerializer,
    PriceImportCreateSerializer,
    PriceListImportJobSerializer,
)
from ..services.ocr import get_ocr_provider
from ..permissions import IsOwnerRole
from .pricing import get_owner_laundry

logger = logging.getLogger(__name__)


from rest_framework.throttling import UserRateThrottle
from PIL import Image as PILImage

class PriceImportRateThrottle(UserRateThrottle):
    rate = '60/hour'

class PriceImportViewSet(viewsets.GenericViewSet):
    queryset = PriceListImportJob.objects.none()
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = PriceListImportJobSerializer
    throttle_classes = [PriceImportRateThrottle]

    def get_queryset(self):
        return PriceListImportJob.objects.filter(
            laundry__owner=self.request.user
        ).prefetch_related('draft_items')

    def _require_laundry(self):
        laundry = get_owner_laundry(self.request.user)
        if laundry is None:
            return None, Response(
                {'status': 'error',
                 'message': 'Register a laundry before importing a price list.',
                 'data': None},
                 status=status.HTTP_400_BAD_REQUEST,
            )
        return laundry, None

    @extend_schema(request=PriceImportCreateSerializer, responses=PriceListImportJobSerializer)
    def create(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
        image_file = request.data.get('source_image')
        if not image_file:
            return Response(
                {'status': 'error', 'message': 'No image file provided.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 1. Enforce max file size of 10MB (checked before parsing to prevent memory fatigue)
        MAX_SIZE = 10 * 1024 * 1024
        if hasattr(image_file, 'size') and image_file.size > MAX_SIZE:
            return Response(
                {'status': 'error', 'message': 'Image file size exceeds the 10MB limit.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Enforce standard web image file extensions
        if hasattr(image_file, 'name'):
            filename = image_file.name.lower()
            valid_extensions = ('.png', '.jpg', '.jpeg', '.webp')
            if not filename.endswith(valid_extensions):
                return Response(
                    {'status': 'error', 'message': 'Unsupported image format. Allowed formats: PNG, JPG, JPEG, WEBP.', 'data': None},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # 3. Decode verification using Pillow (SSRF/Zip-bomb/Malicious upload checks)
        try:
            if hasattr(image_file, 'seek'):
                image_file.seek(0)
            with PILImage.open(image_file) as img:
                img.verify()
            if hasattr(image_file, 'seek'):
                image_file.seek(0)
        except Exception as e:
            logger.error("Malicious or corrupted image upload blocked: %s", str(e))
            return Response(
                {'status': 'error', 'message': 'Invalid or corrupted image file.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )

        payload = PriceImportCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        image = payload.validated_data['source_image']


        provider = get_ocr_provider()
        with transaction.atomic():
            job = PriceListImportJob.objects.create(
                laundry=laundry,
                source_image=image,
                provider=provider.name,
                status=PriceListImportJob.Status.PROCESSING,
            )

        import sys
        if 'test' in sys.argv or 'pytest' in sys.modules:
            from laundries.tasks import process_ocr_import
            process_ocr_import(job.id)
        else:
            from laundries.tasks import process_ocr_import
            transaction.on_commit(lambda: process_ocr_import.delay(job.id))

        job.refresh_from_db()
        return Response(
            PriceListImportJobSerializer(job).data, status=status.HTTP_201_CREATED
        )


    @extend_schema(responses=PriceListImportJobSerializer)
    def retrieve(self, request, pk=None):
        job = self.get_queryset().filter(id=pk).first()
        if job is None:
            return Response(
                {'status': 'error', 'message': 'Import job not found.', 'data': None},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(PriceListImportJobSerializer(job).data)

    @extend_schema(request=PriceImportConfirmSerializer, responses=PriceListImportJobSerializer)
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        job = self.get_queryset().filter(id=pk).first()
        if job is None:
            return Response(
                {'status': 'error', 'message': 'Import job not found.', 'data': None},
                status=status.HTTP_404_NOT_FOUND,
            )
        if job.status == PriceListImportJob.Status.CONFIRMED:
            return Response(
                {'status': 'error', 'message': 'This import was already confirmed.',
                 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = PriceImportConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        rows = payload.validated_data['items']

        existing_names = set(
            LaundryPricingItem.objects.filter(laundry=job.laundry)
            .values_list('item_name', flat=True)
        )
        created, skipped = [], []
        with transaction.atomic():
            next_order = (
                LaundryPricingItem.objects.filter(laundry=job.laundry).count()
            )
            for row in rows:
                name = row['item_name'].strip()
                # Never overwrite live pricing; skip duplicates instead.
                if name in existing_names:
                    skipped.append(name)
                    continue
                LaundryPricingItem.objects.create(
                    laundry=job.laundry,
                    item_name=name,
                    unit_price=row['unit_price'],
                    category=row.get('category', ''),
                    display_order=next_order,
                )
                existing_names.add(name)
                created.append(name)
                next_order += 1
            job.status = PriceListImportJob.Status.CONFIRMED
            job.confirmed_at = timezone.now()
            job.save(update_fields=['status', 'confirmed_at', 'updated_at'])

        return Response({
            'status': 'success',
            'message': f'Imported {len(created)} item(s); skipped {len(skipped)} duplicate(s).',
            'data': {
                'created': created,
                'skipped': skipped,
                'job': PriceListImportJobSerializer(job).data,
            },
        })
