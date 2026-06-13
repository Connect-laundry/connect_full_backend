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

from django.db.models import Q
from ..models.laundry import Laundry, OwnerAuditLog
from ..models.pricing import (
    LaundryPricingItem, LaundryWeightPricing,
    PricingCatalogVersion, ScheduledPriceChange, DeliveryZonePricing
)
from ..renderers import StandardResponseRenderer
from ..serializers.pricing import (
    LaundryPricingItemSerializer,
    LaundryWeightPricingSerializer,
    PricingItemBulkUpdateSerializer,
    PricingItemReorderSerializer,
    PricingCatalogVersionSerializer,
    ScheduledPriceChangeSerializer,
    DeliveryZonePricingSerializer,
)
from ..permissions import IsOwnerRole


logger = logging.getLogger(__name__)


def get_owner_laundry(user):
    """The owner's single laundry, or None if they haven't registered one."""
    return Laundry.objects.filter(owner=user).first()


class PricingItemViewSet(viewsets.ModelViewSet):
    """CRUD + bulk operations for an owner's per-item pricing catalog."""

    queryset = LaundryPricingItem.objects.none()
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

    @action(detail=False, methods=['get'], url_path='default-categories')
    def default_categories(self, request):
        defaults = [
            "Shirts",
            "Trousers",
            "Dresses",
            "Suits",
            "Bedding",
            "Curtains",
            "Shoes",
            "Household"
        ]
        return Response({
            'status': 'success',
            'message': 'Default categories retrieved.',
            'data': defaults
        })

    @action(detail=False, methods=['get'], url_path='template')
    def download_template(self, request):
        import csv
        from django.http import HttpResponse
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="pricing_catalog_template.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['item_name', 'category', 'unit_price', 'is_active', 'display_order'])
        writer.writerow(['Shirt', 'Shirts', '12.50', 'True', '0'])
        writer.writerow(['Trousers', 'Trousers', '15.00', 'True', '1'])
        
        return response

    @action(detail=False, methods=['post'], url_path='import-bulk')
    def import_bulk(self, request):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
        
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response(
                {'status': 'error', 'message': 'Please upload a CSV or Excel file.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        overwrite = str(request.data.get('overwrite', 'false')).lower() == 'true'
        filename = uploaded_file.name.lower()
        
        items_to_create = []
        
        if filename.endswith('.csv'):
            import csv
            import io
            try:
                decoded_file = uploaded_file.read().decode('utf-8')
                io_string = io.StringIO(decoded_file)
                reader = csv.DictReader(io_string)
                for row in reader:
                    name = row.get('item_name', '').strip()
                    if not name:
                        continue
                    try:
                        price = float(row.get('unit_price', 0))
                    except ValueError:
                        price = 0.0
                    items_to_create.append({
                        'item_name': name,
                        'category': row.get('category', '').strip(),
                        'unit_price': price,
                        'is_active': str(row.get('is_active', 'True')).lower() != 'false',
                        'display_order': int(row.get('display_order', 0))
                    })
            except Exception as exc:
                return Response(
                    {'status': 'error', 'message': f'Failed to parse CSV file: {exc}', 'data': None},
                    status=status.HTTP_400_BAD_REQUEST
                )
        elif filename.endswith('.xlsx'):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(uploaded_file)
                sheet = wb.active
                rows = list(sheet.iter_rows(values_only=True))
                if len(rows) > 1:
                    header = [str(cell).strip().lower() for cell in rows[0]]
                    for row in rows[1:]:
                        row_dict = dict(zip(header, row))
                        name = row_dict.get('item_name', '')
                        if name:
                            name = str(name).strip()
                        if not name:
                            continue
                        try:
                            price = float(row_dict.get('unit_price', 0) or 0)
                        except ValueError:
                            price = 0.0
                        items_to_create.append({
                            'item_name': name,
                            'category': str(row_dict.get('category', '') or '').strip(),
                            'unit_price': price,
                            'is_active': str(row_dict.get('is_active', 'True')).lower() != 'false',
                            'display_order': int(row_dict.get('display_order', 0) or 0)
                        })
            except ImportError:
                return Response(
                    {'status': 'error', 'message': 'Excel parser is not installed on this server.', 'data': None},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            except Exception as exc:
                return Response(
                    {'status': 'error', 'message': f'Failed to parse Excel file: {exc}', 'data': None},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            return Response(
                {'status': 'error', 'message': 'Unsupported file format. Please upload a .csv or .xlsx file.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        created_count = 0
        updated_count = 0
        
        with transaction.atomic():
            # Create a backup version snapshot
            current_items = LaundryPricingItem.objects.filter(laundry=laundry).order_by('display_order', 'item_name')
            current_data = []
            for item in current_items:
                current_data.append({
                    'item_name': item.item_name,
                    'category': item.category,
                    'unit_price': str(item.unit_price),
                    'is_active': item.is_active,
                    'display_order': item.display_order
                })
            last_version = PricingCatalogVersion.objects.filter(laundry=laundry).order_by('-version_number').first()
            next_version = (last_version.version_number + 1) if last_version else 1
            PricingCatalogVersion.objects.create(
                laundry=laundry,
                version_number=next_version,
                items_data=current_data
            )
            
            if overwrite:
                LaundryPricingItem.objects.filter(laundry=laundry).delete()
                
            for item_data in items_to_create:
                item, created = LaundryPricingItem.objects.update_or_create(
                    laundry=laundry,
                    item_name=item_data['item_name'],
                    defaults={
                        'category': item_data['category'],
                        'unit_price': item_data['unit_price'],
                        'is_active': item_data['is_active'],
                        'display_order': item_data['display_order']
                    }
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            
            # Log audit trail
            OwnerAuditLog.objects.create(
                laundry=laundry,
                actor=request.user,
                action='BULK_IMPORT',
                details={
                    'filename': uploaded_file.name,
                    'overwrite': overwrite,
                    'created_count': created_count,
                    'updated_count': updated_count
                }
            )
            
        return Response({
            'status': 'success',
            'message': f'Successfully imported pricing catalog: {created_count} created, {updated_count} updated.',
            'data': {
                'created': created_count,
                'updated': updated_count
            }
        })


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


class PricingCatalogVersionViewSet(viewsets.ModelViewSet):
    queryset = PricingCatalogVersion.objects.none()
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = PricingCatalogVersionSerializer

    def get_queryset(self):
        return PricingCatalogVersion.objects.filter(laundry__owner=self.request.user)

    def _require_laundry(self):
        laundry = get_owner_laundry(self.request.user)
        if laundry is None:
            return None, Response(
                {'status': 'error', 'message': 'Register a laundry before managing pricing versions.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
        return laundry, None

    def create(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        items = LaundryPricingItem.objects.filter(laundry=laundry).order_by('display_order', 'item_name')
        items_data = []
        for item in items:
            items_data.append({
                'item_name': item.item_name,
                'category': item.category,
                'unit_price': str(item.unit_price),
                'is_active': item.is_active,
                'display_order': item.display_order
            })
            
        last_version = PricingCatalogVersion.objects.filter(laundry=laundry).order_by('-version_number').first()
        next_version = (last_version.version_number + 1) if last_version else 1
        
        version = PricingCatalogVersion.objects.create(
            laundry=laundry,
            version_number=next_version,
            items_data=items_data
        )
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='SAVE_VERSION',
            details={'version_number': version.version_number}
        )
        
        return Response(PricingCatalogVersionSerializer(version).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='rollback')
    def rollback(self, request, pk=None):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        version = self.get_queryset().filter(id=pk).first()
        if not version:
            return Response(
                {'status': 'error', 'message': 'Pricing version not found.', 'data': None},
                status=status.HTTP_404_NOT_FOUND
            )
            
        with transaction.atomic():
            current_items = LaundryPricingItem.objects.filter(laundry=laundry).order_by('display_order', 'item_name')
            current_data = []
            for item in current_items:
                current_data.append({
                    'item_name': item.item_name,
                    'category': item.category,
                    'unit_price': str(item.unit_price),
                    'is_active': item.is_active,
                    'display_order': item.display_order
                })
            last_version = PricingCatalogVersion.objects.filter(laundry=laundry).order_by('-version_number').first()
            next_version = (last_version.version_number + 1) if last_version else 1
            PricingCatalogVersion.objects.create(
                laundry=laundry,
                version_number=next_version,
                items_data=current_data
            )
            
            LaundryPricingItem.objects.filter(laundry=laundry).delete()
            
            for row in version.items_data:
                LaundryPricingItem.objects.create(
                    laundry=laundry,
                    item_name=row['item_name'],
                    category=row.get('category', ''),
                    unit_price=row['unit_price'],
                    is_active=row.get('is_active', True),
                    display_order=row.get('display_order', 0)
                )
                
            OwnerAuditLog.objects.create(
                laundry=laundry,
                actor=request.user,
                action='ROLLBACK_VERSION',
                details={'rolled_back_to': version.version_number, 'restored_count': len(version.items_data)}
            )
            
        return Response({
            'status': 'success',
            'message': f'Catalog successfully rolled back to version {version.version_number}.'
        })


class ScheduledPriceChangeViewSet(viewsets.ModelViewSet):
    queryset = ScheduledPriceChange.objects.none()
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = ScheduledPriceChangeSerializer

    def get_queryset(self):
        return ScheduledPriceChange.objects.filter(laundry__owner=self.request.user)

    def _require_laundry(self):
        laundry = get_owner_laundry(self.request.user)
        if laundry is None:
            return None, Response(
                {'status': 'error', 'message': 'Register a laundry before scheduling price changes.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
        return laundry, None

    def create(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change = serializer.save(laundry=laundry)
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='CREATE_SCHEDULED_CHANGE',
            details={'scheduled_id': str(change.id), 'effective_at': str(change.effective_at)}
        )
        
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        instance = self.get_object()
        if instance.is_applied:
            return Response(
                {'status': 'error', 'message': 'Cannot delete an already applied scheduled price change.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='DELETE_SCHEDULED_CHANGE',
            details={'scheduled_id': str(instance.id)}
        )
        
        return super().destroy(request, *args, **kwargs)


class DeliveryZonePricingViewSet(viewsets.ModelViewSet):
    queryset = DeliveryZonePricing.objects.none()
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = DeliveryZonePricingSerializer

    def get_queryset(self):
        return DeliveryZonePricing.objects.filter(laundry__owner=self.request.user)

    def _require_laundry(self):
        laundry = get_owner_laundry(self.request.user)
        if laundry is None:
            return None, Response(
                {'status': 'error', 'message': 'Register a laundry before managing delivery zones.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
        return laundry, None

    def create(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        zone = serializer.save(laundry=laundry)
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='CREATE_DELIVERY_ZONE',
            details={'zone_id': str(zone.id), 'min': str(zone.min_distance_km), 'max': str(zone.max_distance_km)}
        )
        
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        response = super().update(request, *args, **kwargs)
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='UPDATE_DELIVERY_ZONE',
            details={'zone_id': str(kwargs.get('pk'))}
        )
        return response

    def destroy(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        instance = self.get_object()
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='DELETE_DELIVERY_ZONE',
            details={'zone_id': str(instance.id)}
        )
        return super().destroy(request, *args, **kwargs)

