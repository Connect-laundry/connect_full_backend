"""Owner-facing pricing management endpoints.

* ``PricingItemViewSet`` — CRUD + bulk-update + bulk-reorder for the per-item
  catalog (``dashboard/pricing-items/``).
* ``WeightPricingView`` — singleton get/upsert for the weight tariff
  (``dashboard/weight-pricing/``).

All endpoints are owner-scoped: a row is only ever visible/mutable through the
authenticated owner's own laundry.
"""
import logging

# pyre-ignore[missing-module]
from rest_framework import permissions, status, viewsets
# pyre-ignore[missing-module]
from rest_framework.decorators import action
# pyre-ignore[missing-module]
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema

from ..models.laundry import Laundry
from ..models.pricing import LaundryPricingItem, LaundryWeightPricing
from ..renderers import StandardResponseRenderer
from ..serializers.pricing import (
    LaundryPricingItemSerializer,
    LaundryWeightPricingSerializer,
    PricingItemBulkUpdateSerializer,
    PricingItemReorderSerializer,
)
from .my_laundry import IsOwnerRole

logger = logging.getLogger(__name__)


def get_owner_laundry(user):
    """The owner's single laundry, or None if they haven't registered one."""
    return Laundry.objects.filter(owner=user).first()


class PricingItemViewSet(viewsets.ModelViewSet):
    """CRUD + bulk operations for an owner's per-item pricing catalog."""

    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = LaundryPricingItemSerializer

    def get_queryset(self):
        return LaundryPricingItem.objects.filter(laundry__owner=self.request.user)

    def _require_laundry(self):
        laundry = get_owner_laundry(self.request.user)
        if laundry is None:
            return None, Response(
                {
                    'status': 'error',
                    'message': 'Register a laundry before managing pricing.',
                    'data': None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return laundry, None

    def create(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save(laundry=laundry)
        except IntegrityError:
            return Response(
                {
                    'status': 'error',
                    'message': 'A pricing item with this name already exists for your laundry.',
                    'data': None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(request=PricingItemBulkUpdateSerializer, responses=LaundryPricingItemSerializer)
    @action(detail=False, methods=['post'], url_path='bulk-update')
    def bulk_update(self, request):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
        payload = PricingItemBulkUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        rows = payload.validated_data['items']
        ids = [row['id'] for row in rows]
        owned = {item.id: item for item in self.get_queryset().filter(id__in=ids)}
        missing = [str(i) for i in ids if i not in owned]
        if missing:
            return Response(
                {'status': 'error',
                 'message': f'Unknown pricing item id(s): {", ".join(missing)}',
                 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        editable = {'item_name', 'category', 'unit_price', 'is_active', 'display_order'}
        with transaction.atomic():
            for row in rows:
                item = owned[row['id']]
                changed = []
                for field in editable:
                    if field in row:
                        setattr(item, field, row[field])
                        changed.append(field)
                if changed:
                    item.save(update_fields=changed + ['updated_at'])
        data = LaundryPricingItemSerializer(
            self.get_queryset(), many=True, context=self.get_serializer_context()
        ).data
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(request=PricingItemReorderSerializer, responses=LaundryPricingItemSerializer)
    @action(detail=False, methods=['post'], url_path='bulk-reorder')
    def bulk_reorder(self, request):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
        payload = PricingItemReorderSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        rows = payload.validated_data['items']
        owned = {item.id: item for item in self.get_queryset().filter(
            id__in=[r['id'] for r in rows])}
        missing = [str(r['id']) for r in rows if r['id'] not in owned]
        if missing:
            return Response(
                {'status': 'error',
                 'message': f'Unknown pricing item id(s): {", ".join(missing)}',
                 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            for row in rows:
                item = owned[row['id']]
                item.display_order = row['display_order']
                item.save(update_fields=['display_order', 'updated_at'])
        data = LaundryPricingItemSerializer(
            self.get_queryset(), many=True, context=self.get_serializer_context()
        ).data
        return Response(data, status=status.HTTP_200_OK)


class WeightPricingView(APIView):
    """GET / PUT / PATCH the owner's weight-based tariff (singleton per laundry)."""

    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = LaundryWeightPricingSerializer

    def _laundry_or_error(self, request):
        laundry = get_owner_laundry(request.user)
        if laundry is None:
            return None, Response(
                {'status': 'error',
                 'message': 'Register a laundry before configuring weight pricing.',
                 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return laundry, None

    @extend_schema(responses=LaundryWeightPricingSerializer)
    def get(self, request):
        laundry, error = self._laundry_or_error(request)
        if error is not None:
            return error
        pricing = getattr(laundry, 'weight_pricing', None)
        if pricing is None:
            return Response(
                {'status': 'error', 'message': 'No weight pricing configured.', 'data': None},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(LaundryWeightPricingSerializer(pricing).data)

    def _upsert(self, request, *, partial):
        laundry, error = self._laundry_or_error(request)
        if error is not None:
            return error
        instance = getattr(laundry, 'weight_pricing', None)
        serializer = LaundryWeightPricingSerializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(laundry=laundry)
        code = status.HTTP_200_OK if instance else status.HTTP_201_CREATED
        return Response(serializer.data, status=code)

    @extend_schema(request=LaundryWeightPricingSerializer, responses=LaundryWeightPricingSerializer)
    def put(self, request):
        return self._upsert(request, partial=False)

    @extend_schema(request=LaundryWeightPricingSerializer, responses=LaundryWeightPricingSerializer)
    def patch(self, request):
        return self._upsert(request, partial=True)
