"""AI-assisted price-list import endpoints (owner-facing).

Flow (see docs/PRICE_LIST_IMPORT_API.md):
1. ``GET  price-imports/availability/``: whether scanning is available to
   this owner right now (feature flag, rollout allowlist, provider config,
   daily allowance).
2. ``POST price-imports/`` (multipart ``source_image``): the image is hardened
   and extracted *synchronously*; the response carries the finished job.
   No Celery/Redis involved.
3. ``GET  price-imports/{id}/``: fetch the job and its drafts (survives a
   browser refresh).
4. ``POST price-imports/{id}/confirm/``: owner-edited rows; revalidated
   server side, applied atomically, idempotent.
5. ``POST price-imports/{id}/cancel/``: discard drafts.

The laundry is always resolved from the authenticated owner, never from the
request body. Manual pricing endpoints are independent of all of this.
"""
import logging

# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from rest_framework import permissions, status, viewsets
# pyre-ignore[missing-module]
from rest_framework.decorators import action
# pyre-ignore[missing-module]
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
# pyre-ignore[missing-module]
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from config.throttling import UserThrottle
from ..models.price_import import PriceListImportJob
from ..permissions import IsOwnerRole
from ..renderers import StandardResponseRenderer
from ..serializers.price_import import (
    PriceImportConfirmSerializer,
    PriceImportCreateSerializer,
    PriceListImportJobSerializer,
)
from ..services.price_import import service
from ..services.price_import.errors import ImageRejected
from ..services.price_import.image import prepare_image
from .pricing import get_owner_laundry

logger = logging.getLogger(__name__)

MANUAL_HINT = 'You can always add your services manually.'


class PriceImportUploadThrottle(UserThrottle):
    """Per owner account, shared across workers (DB/Redis-backed). The per-laundry
    daily cap in the service is the real cost control; this stops bursts."""
    scope = 'price_import_upload'


def _error(code, message, http_status, data=None):
    return Response(
        {'status': 'error', 'code': code, 'message': message, 'data': data},
        status=http_status,
    )


class PriceImportViewSet(viewsets.GenericViewSet):
    queryset = PriceListImportJob.objects.none()
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = PriceListImportJobSerializer

    def get_throttles(self):
        throttles = super().get_throttles()
        if self.action == 'create':
            throttles.append(PriceImportUploadThrottle())
        return throttles

    def get_queryset(self):
        return service.owner_jobs(self.request.user).prefetch_related('draft_items__matched_item')

    @extend_schema(responses=None)
    @action(detail=False, methods=['get'])
    def availability(self, request):
        # Owners still onboarding have no laundry yet; they can scan too.
        laundry = get_owner_laundry(request.user)
        available, reason = service.ai_available_for(laundry, request.user)
        used = service.imports_last_24h(laundry, request.user)
        return Response({
            'status': 'success',
            'message': 'Price-list scanning availability.',
            'data': {
                'available': available,
                'reason': reason or None,
                'daily_limit': settings.PRICE_LIST_DAILY_LIMIT_PER_LAUNDRY,
                'used_last_24h': used,
                'max_upload_mb': settings.PRICE_LIST_UPLOAD_MAX_MB,
                'accepted_types': ['image/jpeg', 'image/png', 'image/webp'],
                'manual_entry_available': True,
            },
        })

    @extend_schema(request=PriceImportCreateSerializer, responses=PriceListImportJobSerializer)
    def create(self, request, *args, **kwargs):
        laundry = get_owner_laundry(request.user)   # None while onboarding
        available, _reason = service.ai_available_for(laundry, request.user)
        if not available:
            return _error(
                'AI_IMPORT_NOT_AVAILABLE',
                f"Price-list scanning isn't available right now. {MANUAL_HINT}", 403,
            )
        upload = request.FILES.get('source_image')
        if upload is None:
            return _error('NO_FILE', 'Please choose a photo of your price list.', 400)
        try:
            prepared = prepare_image(upload)
        except ImageRejected as exc:
            return _error(exc.code, exc.message, exc.http_status)

        # ?async=1: return 202 at once and extract in the background; the client
        # polls GET {id}/. Keeps proxies/browsers from holding a request open
        # for up to a minute. Default stays synchronous for existing clients.
        wants_async = str(request.query_params.get('async', '')).lower() in ('1', 'true', 'yes')
        background = wants_async and settings.PRICE_LIST_BACKGROUND_MODE == 'thread'
        try:
            outcome = service.start_import(laundry=laundry, user=request.user, image=prepared,
                                           request=request, background=background)
        except service.ImportRefused as exc:
            return _error(exc.code, f'{exc.message}', exc.http_status)

        job = self.get_queryset().get(pk=outcome.job.pk)
        data = PriceListImportJobSerializer(job).data
        data['deduplicated'] = outcome.deduplicated
        if not outcome.created:
            code = status.HTTP_200_OK
        elif wants_async:
            code = status.HTTP_202_ACCEPTED
        else:
            code = status.HTTP_201_CREATED
        return Response(data, status=code)

    @extend_schema(responses=PriceListImportJobSerializer)
    def retrieve(self, request, pk=None):
        service.expire_stale(service.owner_jobs(request.user).filter(id=pk))
        job = self.get_queryset().filter(id=pk).first()
        if job is None:
            return _error('NOT_FOUND', 'Import job not found.', 404)
        return Response(PriceListImportJobSerializer(job).data)

    @extend_schema(request=PriceImportConfirmSerializer, responses=PriceListImportJobSerializer)
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        payload = PriceImportConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            result, replayed = service.confirm_import(
                job_id=pk, owner=request.user,
                rows=[dict(r) for r in payload.validated_data['items']],
                currency_confirmed=payload.validated_data.get('currency_confirmed'),
            )
        except service.ConfirmRejected as exc:
            return _error(exc.code, exc.message, exc.http_status, {'errors': exc.errors} if exc.errors else None)
        job = self.get_queryset().get(pk=pk)
        created, updated, skipped = result['created'], result.get('updated', []), result['skipped']
        message = f'Imported {len(created)} item(s)'
        if updated:
            message += f', updated {len(updated)}'
        message += f'; skipped {len(skipped)} duplicate(s).'
        return Response({
            'status': 'success',
            'message': message,
            'data': {
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'already_confirmed': replayed,
                'job': PriceListImportJobSerializer(job).data,
            },
        })

    @extend_schema(request=None, responses=PriceListImportJobSerializer)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        try:
            service.cancel_import(job_id=pk, owner=request.user)
        except service.ConfirmRejected as exc:
            return _error(exc.code, exc.message, exc.http_status)
        return Response(PriceListImportJobSerializer(self.get_queryset().get(pk=pk)).data)
