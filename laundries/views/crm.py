from rest_framework import views, permissions, status
from rest_framework.response import Response
from django.db.models import Count, Sum, Max
import logging

from ordering.models import Order
from laundries.models.laundry import Laundry
from ..serializers.crm import CustomerSummarySerializer, CustomerProfileSerializer

logger = logging.getLogger(__name__)


class IsLaundryOwner(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.role in ['OWNER', 'ADMIN']


class CustomerListView(views.APIView):
    """
    GET /api/v1/laundries/dashboard/customers/
    List customers who have ordered from this laundry with aggregated stats.
    """
    permission_classes = [IsLaundryOwner]

    def get(self, request):
        laundry = Laundry.objects.filter(owner=request.user).first()
        if not laundry:
            return Response({"success": False, "status": "error",
                            "message": "Laundry not found"}, status=404)

        customers = (
            Order.objects.filter(laundry=laundry)
            .values(
                'user__id', 'user__email', 'user__first_name',
                'user__last_name', 'user__phone'
            )
            .annotate(
                order_count=Count('id'),
                total_spent=Sum('final_price'),
                last_order_date=Max('created_at')
            )
            .order_by('-total_spent')
        )

        data = [{
            'user_id': c['user__id'],
            'email': c['user__email'],
            'first_name': c['user__first_name'],
            'last_name': c['user__last_name'],
            'phone': c['user__phone'],
            'order_count': c['order_count'],
            'total_spent': c['total_spent'] or 0,
            'last_order_date': c['last_order_date'],
        } for c in customers]

        serializer = CustomerSummarySerializer(data, many=True)

        return Response({
            "success": True,
            "message": f"{len(data)} customer(s) found.",
            "data": serializer.data
        })


class CustomerProfileView(views.APIView):
    """
    GET /api/v1/laundries/dashboard/customers/<uuid:user_id>/profile/
    Detailed history for a specific customer at this laundry.
    """
    permission_classes = [IsLaundryOwner]

    def get(self, request, user_id):
        laundry = Laundry.objects.filter(owner=request.user).first()
        if not laundry:
            return Response({"success": False, "status": "error",
                            "message": "Laundry not found"}, status=404)

        orders = Order.objects.filter(
            laundry=laundry, user_id=user_id
        ).select_related('user').order_by('-created_at')

        if not orders.exists():
            return Response({
                "success": False,
                "message": "No orders found for this customer at your laundry."
            }, status=status.HTTP_404_NOT_FOUND)

        first_order = orders.first()
        user = first_order.user

        stats = orders.aggregate(
            order_count=Count('id'),
            total_spent=Sum('final_price')
        )

        order_list = [{
            'order_no': o.order_no,
            'status': o.status,
            'estimated_price': str(o.estimated_price),
            'final_price': str(o.final_price),
            'created_at': o.created_at.isoformat(),
        } for o in orders[:50]]

        profile_data = {
            'user_id': user.id,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'phone': user.phone,
            'order_count': stats['order_count'],
            'total_spent': stats['total_spent'] or 0,
            'orders': order_list,
        }

        serializer = CustomerProfileSerializer(profile_data)

        return Response({
            "success": True,
            "data": serializer.data
        })
