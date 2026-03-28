import uuid
from rest_framework import views, viewsets, status, permissions
from rest_framework.response import Response
from rest_framework import serializers
from django.db.models import Sum
import logging

from .payout_models import BankAccount, PayoutRequest
from ordering.models import Order

logger = logging.getLogger(__name__)


class IsOwner(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('OWNER', 'ADMIN')


# ─── Serializers ──────────────────────────────────────────

class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ('id', 'bank_name', 'account_name', 'account_number',
                  'bank_code', 'is_primary', 'created_at')
        read_only_fields = ('id', 'created_at')


class PayoutRequestSerializer(serializers.ModelSerializer):
    bank_account_display = serializers.SerializerMethodField()

    class Meta:
        model = PayoutRequest
        fields = ('id', 'bank_account', 'bank_account_display', 'amount',
                  'currency', 'status', 'reference', 'notes',
                  'requested_at', 'processed_at')
        read_only_fields = ('id', 'status', 'reference', 'requested_at', 'processed_at')

    def get_bank_account_display(self, obj):
        return str(obj.bank_account) if obj.bank_account else None


class PayoutCreateSerializer(serializers.Serializer):
    bank_account_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


# ─── Views ────────────────────────────────────────────────

class BankAccountView(views.APIView):
    """
    POST /api/v1/payments/payouts/bank-account/ → Link a bank account
    GET  /api/v1/payments/payouts/bank-account/ → List linked accounts
    """
    permission_classes = [IsOwner]

    def get(self, request):
        accounts = BankAccount.objects.filter(owner=request.user)
        serializer = BankAccountSerializer(accounts, many=True)
        return Response({
            "status": "success",
            "data": serializer.data
        })

    def post(self, request):
        serializer = BankAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(owner=request.user)

        logger.info(f"Bank account linked by {request.user.email}")

        return Response({
            "status": "success",
            "message": "Bank account linked successfully.",
            "data": serializer.data
        }, status=status.HTTP_201_CREATED)


class PayoutRequestView(views.APIView):
    """
    POST /api/v1/payments/payouts/request/ → Initiate a payout
    """
    permission_classes = [IsOwner]

    def post(self, request):
        serializer = PayoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        bank_account_id = serializer.validated_data['bank_account_id']
        amount = serializer.validated_data['amount']
        notes = serializer.validated_data.get('notes', '')

        # Validate bank account belongs to owner
        try:
            bank_account = BankAccount.objects.get(id=bank_account_id, owner=request.user)
        except BankAccount.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Bank account not found."
            }, status=status.HTTP_404_NOT_FOUND)

        # Check available balance (simplified: sum of DELIVERED/COMPLETED orders)
        from laundries.models.laundry import Laundry
        laundry = Laundry.objects.filter(owner=request.user).first()
        if not laundry:
            return Response({
                "status": "error",
                "message": "No laundry found."
            }, status=status.HTTP_400_BAD_REQUEST)

        total_earned = Order.objects.filter(
            laundry=laundry, status__in=['DELIVERED', 'COMPLETED']
        ).aggregate(total=Sum('final_price'))['total'] or 0

        total_paid_out = PayoutRequest.objects.filter(
            owner=request.user, status__in=['PENDING', 'PROCESSING', 'COMPLETED']
        ).aggregate(total=Sum('amount'))['total'] or 0

        available_balance = total_earned - total_paid_out

        if amount > available_balance:
            return Response({
                "status": "error",
                "message": f"Insufficient balance. Available: {available_balance}"
            }, status=status.HTTP_400_BAD_REQUEST)

        payout = PayoutRequest.objects.create(
            owner=request.user,
            bank_account=bank_account,
            amount=amount,
            reference=f"PO-{uuid.uuid4().hex[:12].upper()}",
            notes=notes,
        )

        logger.info(f"Payout requested: {payout.reference} for {amount} by {request.user.email}")

        return Response({
            "status": "success",
            "message": "Payout request submitted.",
            "data": PayoutRequestSerializer(payout).data
        }, status=status.HTTP_201_CREATED)


class PayoutHistoryView(views.APIView):
    """
    GET /api/v1/payments/payouts/history/ → Track payout statuses
    """
    permission_classes = [IsOwner]

    def get(self, request):
        payouts = PayoutRequest.objects.filter(owner=request.user)
        serializer = PayoutRequestSerializer(payouts, many=True)
        return Response({
            "status": "success",
            "data": serializer.data
        })
