"""Admin Operations Center API.

Endpoints (all ADMIN-only, session- or JWT-authenticated):

    GET  /api/v1/admin/search/?q=...          universal grouped search
    GET  /api/v1/admin/notifications/         admin notification feed
    GET  /api/v1/admin/notifications/unread-count/
    POST /api/v1/admin/notifications/<id>/read/
    POST /api/v1/admin/notifications/read-all/
    GET  /api/v1/admin/audit-log/             filterable audit trail

Search uses case-insensitive partial matching (portable across PostgreSQL and
SQLite). For very large datasets a PostgreSQL trigram/full-text index can be
layered on top of the same fields; the queried columns are already indexed.
"""
import logging

# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from rest_framework.authentication import SessionAuthentication
# pyre-ignore[missing-module]
from rest_framework_simplejwt.authentication import JWTAuthentication
# pyre-ignore[missing-module]
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
# pyre-ignore[missing-module]
from django.db.models import Q
# pyre-ignore[missing-module]
from django.shortcuts import get_object_or_404
# pyre-ignore[missing-module]
from django.utils.dateparse import parse_datetime

from config.throttling import AdminSearchThrottle
from marketplace.models import Notification, AuditLog
from marketplace.permissions import IsPlatformAdmin
from marketplace.serializers import NotificationSerializer
from marketplace.services.audit import record_audit

logger = logging.getLogger(__name__)

MAX_PER_GROUP = 10
ABS_MAX_PER_GROUP = 50


class AdminSearchItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    sublabel = serializers.CharField(allow_blank=True, required=False)
    role = serializers.CharField(allow_blank=True, required=False)
    phone = serializers.CharField(allow_blank=True, required=False)
    status = serializers.CharField(allow_blank=True, required=False)
    action_url = serializers.CharField()


class AdminSearchDataSerializer(serializers.Serializer):
    query = serializers.CharField()
    total = serializers.IntegerField()
    users = AdminSearchItemSerializer(many=True)
    orders = AdminSearchItemSerializer(many=True)
    laundries = AdminSearchItemSerializer(many=True)
    payments = AdminSearchItemSerializer(many=True)
    reviews = AdminSearchItemSerializer(many=True)
    coupons = AdminSearchItemSerializer(many=True)


class AdminSearchResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = AdminSearchDataSerializer()


class AdminNotificationListDataSerializer(serializers.Serializer):
    results = NotificationSerializer(many=True)
    total = serializers.IntegerField()
    unread = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class AdminNotificationListResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = AdminNotificationListDataSerializer()


class AdminUnreadDataSerializer(serializers.Serializer):
    unread = serializers.IntegerField()


class AdminUnreadResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = AdminUnreadDataSerializer()


class AdminNotificationResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = NotificationSerializer()


class AdminMarkAllReadDataSerializer(serializers.Serializer):
    updated = serializers.IntegerField()


class AdminMarkAllReadResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = AdminMarkAllReadDataSerializer()


class AdminAuditLogItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    action = serializers.CharField()
    actor_email = serializers.EmailField(allow_blank=True)
    target_type = serializers.CharField(allow_blank=True)
    target_id = serializers.CharField(allow_blank=True)
    target_repr = serializers.CharField(allow_blank=True)
    metadata = serializers.JSONField()
    ip_address = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class AdminAuditLogDataSerializer(serializers.Serializer):
    results = AdminAuditLogItemSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class AdminAuditLogResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = AdminAuditLogDataSerializer()


class _AdminBase(APIView):
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsPlatformAdmin]


def _clamp_limit(raw):
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return MAX_PER_GROUP
    return max(1, min(limit, ABS_MAX_PER_GROUP))


class AdminSearchView(_AdminBase):
    throttle_classes = [AdminSearchThrottle]

    @extend_schema(
        parameters=[
            OpenApiParameter('q', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('limit', OpenApiTypes.INT, OpenApiParameter.QUERY),
        ],
        responses=AdminSearchResponseSerializer,
    )
    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        limit = _clamp_limit(request.query_params.get('limit'))

        if len(q) < 2:
            return Response({
                'status': 'success',
                'message': 'Enter at least 2 characters to search.',
                'data': self._empty(),
            })

        data = {
            'query': q,
            'users': self._users(q, limit),
            'orders': self._orders(q, limit),
            'laundries': self._laundries(q, limit),
            'payments': self._payments(q, limit),
            'reviews': self._reviews(q, limit),
            'coupons': self._coupons(q, limit),
        }
        data['total'] = sum(
            len(v) for k, v in data.items() if isinstance(v, list)
        )

        record_audit(
            action=AuditLog.Action.ADMIN_SEARCH,
            request=request,
            metadata={'q': q, 'results': data['total']},
        )

        return Response({'status': 'success', 'message': 'Search complete.', 'data': data})

    @staticmethod
    def _empty():
        return {
            'query': '', 'total': 0, 'users': [], 'orders': [], 'laundries': [],
            'payments': [], 'reviews': [], 'coupons': [],
        }

    @staticmethod
    def _users(q, limit):
        from users.models import User
        qs = User.objects.filter(
            Q(email__icontains=q) | Q(phone__icontains=q)
            | Q(first_name__icontains=q) | Q(last_name__icontains=q)
        ).order_by('-created_at')[:limit]
        return [{
            'id': str(u.id),
            'label': u.get_full_name() or u.email,
            'sublabel': u.email,
            'role': u.role,
            'phone': u.phone,
            'action_url': f'/admin/users/user/{u.id}/change/',
        } for u in qs]

    @staticmethod
    def _orders(q, limit):
        from ordering.models import Order
        qs = (
            Order.objects.select_related('user', 'laundry')
            .filter(Q(order_no__icontains=q) | Q(user__email__icontains=q))
            .order_by('-created_at')[:limit]
        )
        return [{
            'id': str(o.id),
            'label': o.order_no,
            'sublabel': f'{o.status} · {o.user.email if o.user else "?"}',
            'status': o.status,
            'action_url': f'/admin/ordering/order/{o.id}/change/',
        } for o in qs]

    @staticmethod
    def _laundries(q, limit):
        from laundries.models.laundry import Laundry
        qs = (
            Laundry.objects.select_related('owner')
            .filter(Q(name__icontains=q) | Q(address__icontains=q) | Q(city__icontains=q))
            .order_by('-created_at')[:limit]
        )
        return [{
            'id': str(l.id),
            'label': l.name,
            'sublabel': l.city or l.address,
            'status': getattr(l, 'status', ''),
            'action_url': f'/admin/laundries/laundry/{l.id}/change/',
        } for l in qs]

    @staticmethod
    def _payments(q, limit):
        from payments.models import Payment
        qs = (
            Payment.objects.select_related('user')
            .filter(
                Q(transaction_reference__icontains=q)
                | Q(paystack_reference__icontains=q)
                | Q(user__email__icontains=q)
            )
            .order_by('-created_at')[:limit]
        )
        return [{
            'id': str(p.id),
            'label': p.transaction_reference,
            'sublabel': f'{p.status} · {p.amount} {p.currency}',
            'status': p.status,
            'action_url': f'/admin/payments/payment/{p.id}/change/',
        } for p in qs]

    @staticmethod
    def _reviews(q, limit):
        from laundries.models.review import Review
        qs = (
            Review.objects.select_related('user', 'laundry')
            .filter(
                Q(comment__icontains=q)
                | Q(user__email__icontains=q)
                | Q(laundry__name__icontains=q)
            )
            .order_by('-created_at')[:limit]
        )
        return [{
            'id': str(r.id),
            'label': f'{r.rating}★ {r.laundry.name if r.laundry else ""}',
            'sublabel': (r.comment or '')[:80],
            'action_url': f'/admin/laundries/review/{r.id}/change/',
        } for r in qs]

    @staticmethod
    def _coupons(q, limit):
        from ordering.models.coupons import Coupon
        qs = Coupon.objects.filter(code__icontains=q).order_by('-created_at')[:limit]
        return [{
            'id': str(c.id),
            'label': c.code,
            'sublabel': f'{c.discount_type} · {c.discount_value}',
            'action_url': f'/admin/ordering/coupon/{c.id}/change/',
        } for c in qs]


def _admin_feed_qs():
    return Notification.objects.filter(audience=Notification.Audience.ADMIN)


class AdminNotificationListView(_AdminBase):
    @extend_schema(
        parameters=[
            OpenApiParameter('category', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('unread', OpenApiTypes.BOOL, OpenApiParameter.QUERY),
            OpenApiParameter('limit', OpenApiTypes.INT, OpenApiParameter.QUERY),
            OpenApiParameter('offset', OpenApiTypes.INT, OpenApiParameter.QUERY),
        ],
        responses=AdminNotificationListResponseSerializer,
    )
    def get(self, request):
        qs = _admin_feed_qs()

        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        unread_only = request.query_params.get('unread') in ('1', 'true', 'True')
        if unread_only:
            qs = qs.filter(is_read=False)

        try:
            limit = max(1, min(int(request.query_params.get('limit', 20)), 100))
        except (TypeError, ValueError):
            limit = 20
        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
        except (TypeError, ValueError):
            offset = 0

        total = qs.count()
        unread = _admin_feed_qs().filter(is_read=False).count()
        items = qs[offset:offset + limit]

        return Response({
            'status': 'success',
            'message': 'Notifications retrieved.',
            'data': {
                'results': NotificationSerializer(items, many=True).data,
                'total': total,
                'unread': unread,
                'limit': limit,
                'offset': offset,
            },
        })


class AdminNotificationUnreadCountView(_AdminBase):
    @extend_schema(responses=AdminUnreadResponseSerializer)
    def get(self, request):
        unread = _admin_feed_qs().filter(is_read=False).count()
        return Response({
            'status': 'success',
            'message': 'Unread count retrieved.',
            'data': {'unread': unread},
        })


class AdminNotificationMarkReadView(_AdminBase):
    @extend_schema(request=None, responses=AdminNotificationResponseSerializer)
    def post(self, request, pk):
        notification = get_object_or_404(_admin_feed_qs(), pk=pk)
        notification.mark_as_read()
        record_audit(
            action=AuditLog.Action.NOTIFICATION_DISMISSED,
            request=request,
            target_type='Notification',
            target_id=str(notification.id),
            target_repr=notification.title,
        )
        return Response({
            'status': 'success',
            'message': 'Notification marked as read.',
            'data': NotificationSerializer(notification).data,
        })


class AdminNotificationMarkAllReadView(_AdminBase):
    @extend_schema(request=None, responses=AdminMarkAllReadResponseSerializer)
    def post(self, request):
        from django.utils import timezone
        updated = _admin_feed_qs().filter(is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
        return Response({
            'status': 'success',
            'message': 'All notifications marked as read.',
            'data': {'updated': updated},
        })


class AdminAuditLogView(_AdminBase):
    @extend_schema(
        parameters=[
            OpenApiParameter('action', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('actor', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('target_type', OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter('from', OpenApiTypes.DATETIME, OpenApiParameter.QUERY),
            OpenApiParameter('to', OpenApiTypes.DATETIME, OpenApiParameter.QUERY),
            OpenApiParameter('limit', OpenApiTypes.INT, OpenApiParameter.QUERY),
            OpenApiParameter('offset', OpenApiTypes.INT, OpenApiParameter.QUERY),
        ],
        responses=AdminAuditLogResponseSerializer,
    )
    def get(self, request):
        qs = AuditLog.objects.select_related('actor').all()

        action = request.query_params.get('action')
        if action:
            qs = qs.filter(action=action)

        actor_email = request.query_params.get('actor')
        if actor_email:
            qs = qs.filter(actor_email__icontains=actor_email)

        target_type = request.query_params.get('target_type')
        if target_type:
            qs = qs.filter(target_type=target_type)

        date_from = request.query_params.get('from')
        if date_from:
            parsed = parse_datetime(date_from)
            if parsed:
                qs = qs.filter(created_at__gte=parsed)

        date_to = request.query_params.get('to')
        if date_to:
            parsed = parse_datetime(date_to)
            if parsed:
                qs = qs.filter(created_at__lte=parsed)

        try:
            limit = max(1, min(int(request.query_params.get('limit', 50)), 200))
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
        except (TypeError, ValueError):
            offset = 0

        total = qs.count()
        items = qs[offset:offset + limit]
        results = [{
            'id': str(a.id),
            'action': a.action,
            'actor_email': a.actor_email,
            'target_type': a.target_type,
            'target_id': a.target_id,
            'target_repr': a.target_repr,
            'metadata': a.metadata,
            'ip_address': a.ip_address,
            'created_at': a.created_at.isoformat(),
        } for a in items]

        return Response({
            'status': 'success',
            'message': 'Audit log retrieved.',
            'data': {'results': results, 'total': total, 'limit': limit, 'offset': offset},
        })
