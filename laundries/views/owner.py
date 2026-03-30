# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status, decorators
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from django.utils import timezone
import logging

# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.opening_hours import OpeningHours
# pyre-ignore[missing-module]
from ..models.review import Review
# pyre-ignore[missing-module]
from ..models.service import LaundryService
# pyre-ignore[missing-module]
from ..serializers.owner import OwnerLaundrySerializer, OpeningHoursSerializer
# pyre-ignore[missing-module]
from ..serializers.review import ReviewSerializer
# pyre-ignore[missing-module]
from ..serializers.laundry_detail import LaundryServiceSerializer

logger = logging.getLogger(__name__)


class IsOwner(permissions.BasePermission):
    """Only authenticated users with OWNER or ADMIN role."""

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in (
            'OWNER', 'ADMIN')


class OwnerLaundryViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for laundry owners to manage their storefront.

    - POST   /  → Create a new laundry (auto-assigned to current user)
    - GET    /  → List owner's laundries
    - GET    /{id}/ → Retrieve detail
    - PATCH  /{id}/ → Update fields
    - PUT    /{id}/ → Full update

    Nested actions:
    - GET/PUT  /{id}/hours/      → Manage opening hours
    - PATCH    /{id}/toggle/     → Toggle store open/closed
    - GET      /{id}/reviews/    → List reviews for this laundry
    """
    serializer_class = OwnerLaundrySerializer
    permission_classes = [IsOwner]
    http_method_names = ['get', 'post', 'patch', 'put', 'head', 'options']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Laundry.objects.none()
        # Admins see all, owners see only their own
        if self.request.user.is_staff:
            return Laundry.objects.all().prefetch_related('opening_hours')
        return Laundry.objects.filter(
            owner=self.request.user).prefetch_related('opening_hours')

    def perform_create(self, serializer):
        serializer.save()

    def create(self, request, *args, **kwargs):
        # Prevent duplicate: one owner, one laundry (for now)
        if Laundry.objects.filter(owner=request.user).exists():
            return Response({
                "success": False,
                "status": "error",
                "message": "You already have a registered laundry. Use PATCH to update it."
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        logger.info(
            f"Laundry created by {
                request.user.email}: {
                serializer.data.get('name')}")

        return Response({
            "success": True,
            "message": "Laundry storefront created successfully. It is now pending admin approval.",
            "data": serializer.data
        }, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        logger.info(f"Laundry {instance.id} updated by {request.user.email}")

        return Response({
            "success": True,
            "message": "Laundry updated successfully.",
            "data": serializer.data
        })

    # ─── Opening Hours ──────────────────────────────────────

    @decorators.action(detail=True, methods=['get', 'put'], url_path='hours')
    def hours(self, request, pk=None):
        """
        GET:  Returns the weekly schedule for this laundry.
        PUT:  Replaces the entire weekly schedule (send all 7 days).
        """
        laundry = self.get_object()

        if request.method == 'GET':
            hours = laundry.opening_hours.all()
            serializer = OpeningHoursSerializer(hours, many=True)
            return Response({
                "success": True,
                "message": "Opening hours retrieved.",
                "data": serializer.data
            })

        # PUT: Replace all hours
        serializer = OpeningHoursSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            laundry.opening_hours.all().delete()
            for hour_data in serializer.validated_data:
                OpeningHours.objects.create(laundry=laundry, **hour_data)

        # Re-fetch and return
        updated_hours = laundry.opening_hours.all()
        response_serializer = OpeningHoursSerializer(updated_hours, many=True)

        logger.info(
            f"Opening hours updated for laundry {
                laundry.id} by {
                request.user.email}")

        return Response({
            "success": True,
            "message": "Opening hours updated successfully.",
            "data": response_serializer.data
        })

    # ─── Activation & Toggle ────────────────────────────────────────

    # ─── Services / Pricing ────────────────────────────────────

    @decorators.action(detail=True,
                       methods=['get', 'post'], url_path='services')
    def services(self, request, pk=None):
        """
        Manage pricing for items in this laundry.
        GET: List all services
        POST: Create/Update a service pricing
        """
        laundry = self.get_object()

        if request.method == 'GET':
            qs = laundry.laundry_services.select_related(
                'item', 'service_type').all()
            serializer = LaundryServiceSerializer(
                qs, many=True, context={'request': request})
            return Response({
                "success": True,
                "message": "Laundry services retrieved successfully.",
                "data": serializer.data
            })

        elif request.method == 'POST':
            item_id = request.data.get('item_id')
            service_type_id = request.data.get('service_type_id')
            price = request.data.get('price')
            estimated_duration = request.data.get(
                'estimated_duration', '24 hours')
            is_available = request.data.get('is_available', True)

            if not all([item_id, service_type_id, price]):
                return Response(
                    {
                        "success": False,
                        "status": "error",
                        "message": "item_id, service_type_id, and price are required."},
                    status=status.HTTP_400_BAD_REQUEST)

            laundry_service, created = LaundryService.objects.update_or_create(
                laundry=laundry,
                item_id=item_id,
                service_type_id=service_type_id,
                defaults={
                    'price': price,
                    'estimated_duration': estimated_duration,
                    'is_available': is_available
                }
            )

            serializer = LaundryServiceSerializer(
                laundry_service, context={'request': request})
            return Response({
                "success": True,
                "message": f"Service pricing {'created' if created else 'updated'} successfully.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    # ─── Activation ──────────────────────────────────────────

    @decorators.action(detail=True, methods=['patch'], url_path='activate')
    def activate(self, request, pk=None):
        """
        Explicitly activate the store for orders.
        Perform deep validation before allowing is_active = True.
        """
        laundry = self.get_object()

        if laundry.status != Laundry.ApprovalStatus.APPROVED:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": f"Cannot activate a laundry that is '{
                        laundry.status}'. Only approved laundries can be activated."},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            laundry.is_active = True
            laundry.save()  # This triggers clean() -> validate_laundry_ready_for_business()

            logger.info(
                f"Laundry {
                    laundry.id} activated by {
                    request.user.email}")
            return Response({
                "success": True,
                "message": "Laundry activated successfully. Customers can now place orders.",
                "data": {"id": str(laundry.id), "is_active": True}
            })
        except Exception as e:
            from django.core.exceptions import ValidationError as DjangoValidationError
            if isinstance(e, DjangoValidationError):
                return Response({
                    "success": False,
                    "status": "error",
                    "message": "Activation failed. Please check your configuration.",
                    "errors": e.message_dict
                }, status=status.HTTP_400_BAD_REQUEST)
            raise e

    @decorators.action(detail=True, methods=['patch'], url_path='toggle')
    def toggle(self, request, pk=None):
        """
        Toggle the store's is_active status (Open ↔ Closed).
        Only works for laundries that have been APPROVED by admin.
        """
        laundry = self.get_object()

        if laundry.status != Laundry.ApprovalStatus.APPROVED:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": f"Cannot toggle a laundry with status '{
                        laundry.status}'. Only approved laundries can be toggled."},
                status=status.HTTP_400_BAD_REQUEST)

        new_status = not laundry.is_active
        laundry.is_active = new_status

        if laundry.is_active:
            laundry.deactivated_at = None
            laundry.deactivation_reason = None
        else:
            laundry.deactivated_at = timezone.now()
            laundry.deactivation_reason = request.data.get(
                'reason', 'Temporarily closed by owner')

        try:
            laundry.save()  # Triggers validation if new_status is True
            state = "open" if laundry.is_active else "closed"
            logger.info(
                f"Laundry {
                    laundry.id} toggled to {state} by {
                    request.user.email}")

            return Response({
                "success": True,
                "message": f"Store is now {state}.",
                "data": {"id": str(laundry.id), "is_active": laundry.is_active, "name": laundry.name}
            })
        except Exception as e:
            from django.core.exceptions import ValidationError as DjangoValidationError
            if isinstance(e, DjangoValidationError):
                return Response({
                    "success": False,
                    "status": "error",
                    "message": "Cannot open store. Please check your configuration.",
                    "errors": e.message_dict
                }, status=status.HTTP_400_BAD_REQUEST)
            raise e

    # ─── Owner Reviews ───────────────────────────────────────

    @decorators.action(detail=True, methods=['get'], url_path='reviews')
    def reviews(self, request, pk=None):
        """
        List all customer reviews for this laundry (owner-facing).
        """
        laundry = self.get_object()
        reviews = Review.objects.filter(
            laundry=laundry).select_related('user').order_by('-created_at')

        # Simple pagination
        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(reviews, request)

        if page is not None:
            serializer = ReviewSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = ReviewSerializer(reviews, many=True)
        return Response({
            "success": True,
            "message": "Reviews retrieved.",
            "data": serializer.data
        })
