"""Seller Center API for a laundry owner's booking SMS alerts.

Every endpoint resolves the laundry from the signed-in owner
(``Laundry.owner == request.user``), exactly like the rest of the dashboard.
No laundry id is accepted from the client, so an owner can only ever read or
change their own laundry's settings and alert history.

The full number is never returned: the owner sees it masked, which is enough
to recognize it. No provider credential or message text is exposed.
"""
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from laundries.models.laundry import Laundry

from . import conf
from .models import AdminNotificationDelivery as Delivery
from .models import MerchantNotificationSettings
from .phone import mask_recipient
from .services import merchant

RECENT_LIMIT = 10


class IsLaundryOwnerRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'OWNER')


def _laundry_for(user):
    # Same resolution as DashboardBaseView.get_laundry, so settings and the
    # orders list always describe the same laundry.
    return Laundry.objects.filter(owner=user).first()


def _status_label(delivery) -> str:
    if delivery.status == Delivery.Status.BLOCKED:
        return 'Not sent'
    if delivery.status == Delivery.Status.DELIVERED:
        return 'Delivered'
    if delivery.status in (Delivery.Status.SUBMITTED, Delivery.Status.SENT):
        return 'Sent'
    if delivery.status in Delivery.ACTIVE_STATUSES:
        return 'Sending'
    if delivery.status == Delivery.Status.UNDELIVERED:
        return 'Not delivered'
    if delivery.status == Delivery.Status.UNKNOWN:
        return 'Unconfirmed'
    return 'Failed'


def _payload(laundry, user) -> dict:
    row = MerchantNotificationSettings.objects.filter(laundry=laundry).first()
    account = merchant.account_phone(user)
    recent = (
        Delivery.objects.filter(laundry=laundry, audience=Delivery.Audience.MERCHANT)
        .select_related('event').order_by('-created_at')[:RECENT_LIMIT]
    )
    return {
        'available': conf.merchant_sms_enabled() and conf.merchant_sms_allowed_for(laundry.pk),
        'sms_enabled': row.sms_enabled if row else True,
        'phone_masked': mask_recipient(row.phone) if row and row.phone else '',
        'phone_verified': bool(row and row.is_verified),
        'verification_method': row.verification_method if row else '',
        'verified_at': row.phone_verified_at if row else None,
        'pending_phone_masked': mask_recipient(row.pending_phone) if row and row.pending_phone else '',
        'pending_expires_at': row.otp_expires_at if row and row.pending_phone else None,
        'account_phone_masked': mask_recipient(account) if account and (not row or row.phone != account) else '',
        'recent': [
            {
                'id': str(d.id),
                'order_id': str(d.event.order_id),
                'order_no': (d.event.payload or {}).get('order', {}).get('order_no', ''),
                'kind': merchant.TEMPLATE_LABELS.get(d.template, 'Alert'),
                'status': _status_label(d),
                'reason': d.last_error_safe_message if d.status == Delivery.Status.BLOCKED else '',
                'created_at': d.created_at,
                'delivered_at': d.delivered_at,
            }
            for d in recent
        ],
    }


def _error(exc: merchant.VerificationError) -> Response:
    return Response({'status': 'error', 'code': exc.code, 'message': str(exc)}, status=exc.status)


class _OwnerView(APIView):
    permission_classes = [IsLaundryOwnerRole]

    def laundry_or_404(self, request):
        laundry = _laundry_for(request.user)
        if laundry is None:
            return None, Response({'status': 'error', 'message': 'No laundry on this account.'},
                                  status=status.HTTP_404_NOT_FOUND)
        return laundry, None

    def ok(self, laundry, request, code=status.HTTP_200_OK):
        return Response({'status': 'success', 'data': _payload(laundry, request.user)}, status=code)


class _EnabledSerializer(serializers.Serializer):
    sms_enabled = serializers.BooleanField()


class _PhoneSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)


class _CodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=6)


class MerchantSmsSettingsView(_OwnerView):
    def get(self, request):
        laundry, missing = self.laundry_or_404(request)
        return missing or self.ok(laundry, request)

    def patch(self, request):
        laundry, missing = self.laundry_or_404(request)
        if missing:
            return missing
        body = _EnabledSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        merchant.set_enabled(laundry, request.user, body.validated_data['sms_enabled'])
        return self.ok(laundry, request)


class MerchantSmsVerifyStartView(_OwnerView):
    def post(self, request):
        laundry, missing = self.laundry_or_404(request)
        if missing:
            return missing
        body = _PhoneSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            merchant.start_verification(laundry, request.user, body.validated_data['phone'])
        except merchant.VerificationError as exc:
            return _error(exc)
        return self.ok(laundry, request, code=status.HTTP_202_ACCEPTED)


class MerchantSmsVerifyConfirmView(_OwnerView):
    def post(self, request):
        laundry, missing = self.laundry_or_404(request)
        if missing:
            return missing
        body = _CodeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            merchant.confirm_verification(laundry, request.user, body.validated_data['code'])
        except merchant.VerificationError as exc:
            return _error(exc)
        return self.ok(laundry, request)


class MerchantSmsUseAccountPhoneView(_OwnerView):
    def post(self, request):
        laundry, missing = self.laundry_or_404(request)
        if missing:
            return missing
        try:
            merchant.adopt_account_phone(laundry, request.user)
        except merchant.VerificationError as exc:
            return _error(exc)
        return self.ok(laundry, request)


class MerchantSmsTestView(_OwnerView):
    def post(self, request):
        laundry, missing = self.laundry_or_404(request)
        if missing:
            return missing
        try:
            merchant.send_test(laundry, request.user)
        except merchant.VerificationError as exc:
            return _error(exc)
        return self.ok(laundry, request, code=status.HTTP_202_ACCEPTED)
