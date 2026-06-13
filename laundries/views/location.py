"""Owner-facing location/geocoding and operating-hours helper endpoints.

* ``GeocodeView`` — resolve an address to coordinates (forward) or coordinates to
  a formatted address (reverse) using the configured provider. Lets the frontend
  offer address search / manual entry while keeping coordinates backend-derived.
* ``HoursTemplateView`` — apply the industry-standard default operating-hours
  template to the owner's laundry in one call.
"""
import logging

# pyre-ignore[missing-module]
from rest_framework import permissions, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from ..renderers import StandardResponseRenderer
from ..serializers.location import (
    GeocodeRequestSerializer,
    GeocodeResultSerializer,
)
from ..serializers.my_laundry import (
    OPERATING_HOURS_DEFAULT_TEMPLATE,
    MyLaundrySerializer,
)
from ..services.geocoding import GeocodingError, GeocodingUnavailable, get_geocoder
from ..permissions import IsOwnerRole
from .pricing import get_owner_laundry

logger = logging.getLogger(__name__)


class GeocodeView(APIView):
    """POST an address (forward) or lat+lng (reverse) -> normalized location."""

    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = GeocodeRequestSerializer

    @extend_schema(request=GeocodeRequestSerializer, responses=GeocodeResultSerializer)
    def post(self, request):
        payload = GeocodeRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        geocoder = get_geocoder()
        try:
            if payload.validated_data.get('address'):
                result = geocoder.geocode(payload.validated_data['address'])
            else:
                result = geocoder.reverse(
                    float(payload.validated_data['latitude']),
                    float(payload.validated_data['longitude']),
                )
        except GeocodingUnavailable:
            return Response(
                {'status': 'error',
                 'message': 'Geocoding is not configured on this server.',
                 'data': None},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except GeocodingError as exc:
            return Response(
                {'status': 'error', 'message': str(exc), 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(GeocodeResultSerializer(result.as_dict()).data)


class HoursTemplateView(APIView):
    """Apply the default operating-hours template to the owner's laundry."""

    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]

    @extend_schema(request=None, responses=MyLaundrySerializer)
    def post(self, request):
        laundry = get_owner_laundry(request.user)
        if laundry is None:
            return Response(
                {'status': 'error',
                 'message': 'Register a laundry before configuring hours.',
                 'data': None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        MyLaundrySerializer._sync_opening_hours(
            laundry,
            [
                {
                    'day': row['day'],
                    'opening_time': row['opening_time'],
                    'closing_time': row['closing_time'],
                    'is_closed': row['is_closed'],
                    'is_overnight': False,
                }
                for row in OPERATING_HOURS_DEFAULT_TEMPLATE
            ],
        )
        laundry.refresh_from_db()
        return Response({
            'status': 'success',
            'message': 'Default operating hours applied.',
            'data': MyLaundrySerializer(laundry, context={'request': request}).data,
        })
