"""
Comprehensive Automatic Owner Payout System Test Suite (Model A: Platform Collection -> Transfer after Completion).

Verifies the entire Ghanaian GHS Paystack payout lifecycle:
1. Phone normalization & Paystack recipient creation (MTN, Telecel, AirtelTigo).
2. Explicit owner confirmation & fraud/IDOR defense.
3. Resilience when Paystack is unreachable during onboarding (PAYOUT_FAILED_RETRYABLE).
4. Server-authoritative order completion triggering zero-Celery automatic payout.
5. In-flight transfer safety: payout account changes never reroute processing transfers.
6. Paystack balance insufficient handling (WAITING_FOR_FUNDS).
7. Webhook state transitions: transfer.success, transfer.failed, transfer.reversed.
8. Webhook signature security and duplicate delivery idempotency.
9. Management command reconciliation against Paystack Transfer API.
"""

from decimal import Decimal
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from laundries.models.laundry import Laundry
from ordering.models import Order
from ordering.services.finance_service import FinanceService
from ordering.services.order_state_machine import OrderStateMachine
from payments.models import OrderSettlement, Payment, Payout
from payments.services.paystack import PaystackService
from payments.services.payout_service import PayoutError, PayoutService, transfer_reference
from payments.services.recipient_service import RecipientService
from payments.services.settlement_service import SettlementService
from payments.webhooks import _handle_transfer_event, paystack_webhook
from users.models import User

from test_payments import _build_order


@pytest.fixture
def auth_owner():
    owner = User.objects.create_user(
        email="owner_payout_test@simame.com",
        phone="+233551057139",
        password="StrongPass123!",
        role=User.Role.OWNER,
    )
    return owner


@pytest.fixture
def ready_laundry(auth_owner):
    laundry = Laundry.objects.create(
        owner=auth_owner,
        name="Apex Express Wash",
        description="Premium laundry in East Legon",
        phone_number="+233551057139",
        address="12 Boundary Rd, East Legon",
        city="Accra",
        latitude=Decimal("5.6350"),
        longitude=Decimal("-0.1550"),
        pricing_model=Laundry.PricingModel.BY_ITEM,
        price_range=Laundry.PriceRange.MEDIUM,
        payout_status=Laundry.PayoutStatus.PAYOUT_READY,
        payout_method=Laundry.PayoutMethod.MOBILE_MONEY,
        payout_provider="MTN",
        payout_phone="+233551057139",
        payout_phone_normalized="+233551057139",
        paystack_recipient_code="RCP_apex_mtn_123",
        payout_confirmed_at=timezone.now(),
        payout_confirmed_by=auth_owner,
    )
    return laundry


@pytest.mark.django_db
class TestRecipientCreationAndOnboarding:
    def test_mtn_recipient_creation(self, auth_owner, ready_laundry):
        ready_laundry.paystack_recipient_code = ""
        ready_laundry.payout_status = Laundry.PayoutStatus.PAYOUT_SETUP_REQUIRED
        ready_laundry.save()

        mock_paystack = MagicMock()
        mock_paystack.create_transfer_recipient.return_value = {
            "status": True,
            "data": {"recipient_code": "RCP_mtn_success_789"},
        }

        res = RecipientService.setup_payout_account(
            laundry=ready_laundry,
            user=auth_owner,
            payout_phone="055 105 7139",
            payout_provider="MTN",
            payout_method=Laundry.PayoutMethod.MOBILE_MONEY,
            confirmed=True,
            paystack_client=mock_paystack,
        )

        assert res["status"] is True
        assert res["recipient_code"] == "RCP_mtn_success_789"

        ready_laundry.refresh_from_db()
        assert ready_laundry.payout_status == Laundry.PayoutStatus.PAYOUT_READY
        assert ready_laundry.is_payout_ready is True
        assert ready_laundry.paystack_recipient_code == "RCP_mtn_success_789"
        assert ready_laundry.payout_phone_normalized == "+233551057139"
        assert ready_laundry.masked_payout_phone == "055 *** 7139"

        # Verify Paystack was called with Ghana national 10-digit format starting with 0
        call_kwargs = mock_paystack.create_transfer_recipient.call_args.kwargs
        assert call_kwargs["account_number"] == "0551057139"
        assert call_kwargs["bank_code"] == "MTN"
        assert call_kwargs["currency"] == "GHS"
        assert call_kwargs["recipient_type"] == "mobile_money"

    def test_telecel_and_airteltigo_providers(self, auth_owner, ready_laundry):
        mock_paystack = MagicMock()
        mock_paystack.create_transfer_recipient.return_value = {
            "status": True,
            "data": {"recipient_code": "RCP_vod_success_456"},
        }

        # Telecel
        RecipientService.setup_payout_account(
            laundry=ready_laundry,
            user=auth_owner,
            payout_phone="0201234567",
            payout_provider="VOD",
            confirmed=True,
            paystack_client=mock_paystack,
        )
        ready_laundry.refresh_from_db()
        assert ready_laundry.paystack_recipient_code == "RCP_vod_success_456"
        assert ready_laundry.payout_provider == "VOD"

        # AirtelTigo
        mock_paystack.create_transfer_recipient.return_value = {
            "status": True,
            "data": {"recipient_code": "RCP_atl_success_123"},
        }
        RecipientService.setup_payout_account(
            laundry=ready_laundry,
            user=auth_owner,
            payout_phone="0261234567",
            payout_provider="ATL",
            confirmed=True,
            paystack_client=mock_paystack,
        )
        ready_laundry.refresh_from_db()
        assert ready_laundry.paystack_recipient_code == "RCP_atl_success_123"
        assert ready_laundry.payout_provider == "ATL"

    def test_bank_account_payout_rejected_until_supported(self, auth_owner, ready_laundry):
        mock_paystack = MagicMock()

        with pytest.raises(ValueError, match="Bank account payouts are not available yet"):
            RecipientService.setup_payout_account(
                laundry=ready_laundry,
                user=auth_owner,
                payout_phone="0551057139",
                payout_provider="MTN",
                payout_method=Laundry.PayoutMethod.BANK_ACCOUNT,
                confirmed=True,
                paystack_client=mock_paystack,
            )

        mock_paystack.create_transfer_recipient.assert_not_called()

    def test_unsupported_momo_provider_rejected_before_paystack(self, auth_owner, ready_laundry):
        mock_paystack = MagicMock()

        with pytest.raises(ValueError, match="MTN, Telecel, or ATMoney"):
            RecipientService.setup_payout_account(
                laundry=ready_laundry,
                user=auth_owner,
                payout_phone="0551057139",
                payout_provider="BOGUS",
                payout_method=Laundry.PayoutMethod.MOBILE_MONEY,
                confirmed=True,
                paystack_client=mock_paystack,
            )

        mock_paystack.create_transfer_recipient.assert_not_called()

    def test_explicit_confirmation_required(self, auth_owner, ready_laundry):
        with pytest.raises(ValueError, match="explicitly confirm"):
            RecipientService.setup_payout_account(
                laundry=ready_laundry,
                user=auth_owner,
                payout_phone="0551057139",
                payout_provider="MTN",
                confirmed=False,
            )

    def test_idempotent_recipient_reuse(self, auth_owner, ready_laundry):
        """Calling setup with the same phone and provider reuses the existing recipient without hitting Paystack."""
        ready_laundry.paystack_recipient_code = "RCP_existing_valid"
        ready_laundry.payout_phone_normalized = "+233551057139"
        ready_laundry.payout_provider = "MTN"
        ready_laundry.payout_status = Laundry.PayoutStatus.PAYOUT_READY
        ready_laundry.save()

        mock_paystack = MagicMock()
        res = RecipientService.setup_payout_account(
            laundry=ready_laundry,
            user=auth_owner,
            payout_phone="0551057139",
            payout_provider="MTN",
            confirmed=True,
            paystack_client=mock_paystack,
        )

        assert res["reused"] is True
        assert res["recipient_code"] == "RCP_existing_valid"
        mock_paystack.create_transfer_recipient.assert_not_called()

    def test_paystack_down_graceful_handling(self, auth_owner, ready_laundry):
        """When Paystack is unreachable, laundry profile is protected with PAYOUT_FAILED_RETRYABLE."""
        mock_paystack = MagicMock()
        mock_paystack.create_transfer_recipient.return_value = {
            "status": False,
            "message": "Paystack connection timed out",
        }

        res = RecipientService.setup_payout_account(
            laundry=ready_laundry,
            user=auth_owner,
            payout_phone="0559998888",
            payout_provider="MTN",
            confirmed=True,
            paystack_client=mock_paystack,
        )

        assert res["status"] is False
        assert res["payout_status"] == Laundry.PayoutStatus.PAYOUT_FAILED_RETRYABLE

        ready_laundry.refresh_from_db()
        assert ready_laundry.payout_status == Laundry.PayoutStatus.PAYOUT_FAILED_RETRYABLE
        assert ready_laundry.is_payout_ready is False
        assert "Paystack connection timed out" in ready_laundry.payout_failure_reason


@pytest.mark.django_db
class TestInFlightProtectionAndAccountChanges:
    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_changing_account_preserves_in_flight_payout(self, auth_owner, ready_laundry):
        """If owner changes payout account while a payout is PROCESSING, that payout stays with the original recipient."""
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        mock_paystack = MagicMock()
        mock_paystack.initiate_transfer.return_value = {
            "status": True,
            "data": {"status": "pending", "transfer_code": "TRF_flight_001"},
        }

        # Build and send payout to initial recipient RCP_apex_mtn_123
        payout = SettlementService.build_payout(ready_laundry)
        PayoutService.send(payout, paystack=mock_paystack)

        payout.refresh_from_db()
        assert payout.status == Payout.Status.PROCESSING
        assert payout.recipient_code_used == "RCP_apex_mtn_123"

        # Owner now updates their payout destination to Telecel
        mock_paystack.create_transfer_recipient.return_value = {
            "status": True,
            "data": {"recipient_code": "RCP_new_telecel_777"},
        }
        RecipientService.setup_payout_account(
            laundry=ready_laundry,
            user=auth_owner,
            payout_phone="0209990000",
            payout_provider="VOD",
            confirmed=True,
            paystack_client=mock_paystack,
        )

        ready_laundry.refresh_from_db()
        assert ready_laundry.paystack_recipient_code == "RCP_new_telecel_777"

        # Crucial check: In-flight payout still has original recipient code!
        payout.refresh_from_db()
        assert payout.recipient_code_used == "RCP_apex_mtn_123"


@pytest.mark.django_db
class TestAutomaticPayoutLifecycle:
    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True, PAYOUT_AUTOMATIC_ENABLED=True)
    def test_order_completion_triggers_automatic_payout(
        self, auth_owner, ready_laundry, django_capture_on_commit_callbacks
    ):
        _, order = _build_order()
        order.laundry = ready_laundry
        order.payment_status = Order.PaymentStatus.PAID
        order.status = Order.Status.DELIVERED
        # Delivery must be proven before completion can pay out. Money release
        # is gated on this flag (set by a verified handover code or the
        # customer's own confirmation), never on the mere fact of reaching
        # COMPLETED -- see OrderStateMachine.transition.
        order.delivery_confirmed_by_code = True
        order.save()

        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        # Mock Paystack transfer API
        mock_paystack = MagicMock()
        mock_paystack.initiate_transfer.return_value = {
            "status": True,
            "data": {"status": "pending", "transfer_code": "TRF_auto_999"},
        }

        with patch("payments.services.paystack.PaystackService", return_value=mock_paystack):
            with django_capture_on_commit_callbacks(execute=True):
                OrderStateMachine.transition(order.id, Order.Status.COMPLETED, user=auth_owner)

        order.refresh_from_db()
        assert order.status == Order.Status.COMPLETED

        # Check settlement was created and released
        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.SCHEDULED

        # Check payout was created atomically and initiated
        payout = Payout.objects.filter(laundry=ready_laundry).latest("created_at")
        assert payout.status == Payout.Status.PROCESSING
        assert payout.paystack_transfer_code == "TRF_auto_999"
        assert payout.recipient_code_used == "RCP_apex_mtn_123"

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_insufficient_balance_sets_waiting_for_funds(self, ready_laundry):
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        payout = SettlementService.build_payout(ready_laundry)

        # Provider responds balance is insufficient
        mock_paystack = MagicMock()
        mock_paystack.initiate_transfer.return_value = {
            "status": False,
            "message": "Transfer balance is not sufficient for this transaction",
        }

        with pytest.raises(PayoutError):
            PayoutService.send(payout, paystack=mock_paystack)

        payout.refresh_from_db()
        assert payout.status == Payout.Status.WAITING_FOR_FUNDS
        assert "balance" in payout.failure_reason.lower() or "sufficient" in payout.failure_reason.lower()


@pytest.mark.django_db
class TestWebhookStateMachine:
    def test_transfer_success_webhook(self, ready_laundry):
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        payout = SettlementService.build_payout(ready_laundry)
        payout.status = Payout.Status.PROCESSING
        payout.reference = "SIM-PAYOUT-TEST-12345"
        payout.paystack_transfer_code = "TRF_webhook_123"
        payout.save()

        event_data = {
            "data": {
                "reference": "SIM-PAYOUT-TEST-12345",
                "transfer_code": "TRF_webhook_123",
                "amount": int(payout.amount * 100),
                "status": "success",
            }
        }

        resp = _handle_transfer_event(None, "transfer.success", event_data, "dedup-success-001")
        assert resp.status_code == 200

        payout.refresh_from_db()
        assert payout.status == Payout.Status.PAID
        assert payout.paid_at is not None

        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.PAID

    def test_transfer_failed_webhook(self, ready_laundry):
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        payout = SettlementService.build_payout(ready_laundry)
        payout.status = Payout.Status.PROCESSING
        payout.reference = "SIM-PAYOUT-FAIL-123"
        payout.save()

        event_data = {
            "data": {
                "reference": "SIM-PAYOUT-FAIL-123",
                "reason": "Recipient bank server unreachable",
            }
        }

        resp = _handle_transfer_event(None, "transfer.failed", event_data, "dedup-fail-001")
        assert resp.status_code == 200

        payout.refresh_from_db()
        assert payout.status == Payout.Status.FAILED
        assert "Recipient bank server unreachable" in payout.failure_reason

        # Settlements must be returned to PENDING payable pool
        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.PENDING
        assert settlement.payout_id is None

    def test_transfer_reversed_webhook(self, ready_laundry):
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        payout = SettlementService.build_payout(ready_laundry)
        payout.status = Payout.Status.PROCESSING
        payout.reference = "SIM-PAYOUT-REV-123"
        payout.save()

        event_data = {
            "data": {
                "reference": "SIM-PAYOUT-REV-123",
                "reason": "Reversed by mobile money network",
            }
        }

        resp = _handle_transfer_event(None, "transfer.reversed", event_data, "dedup-rev-001")
        assert resp.status_code == 200

        payout.refresh_from_db()
        assert payout.status == Payout.Status.REVERSED
        assert payout.reversed_at is not None

        # Re-credited to payable pool
        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.PENDING

    @override_settings(PAYSTACK_SECRET_KEY="sk_test_simame_webhook_secret_key")
    def test_webhook_signature_verification_security(self):
        factory = APIRequestFactory()
        payload_bytes = json.dumps({"event": "transfer.success", "data": {"reference": "test-ref"}}).encode("utf-8")

        # Case 1: Missing signature -> 401
        req1 = factory.post("/api/v1/payments/webhook/", payload_bytes, content_type="application/json")
        resp1 = paystack_webhook(req1)
        assert resp1.status_code == 401

        # Case 2: Invalid signature -> 401
        req2 = factory.post(
            "/api/v1/payments/webhook/",
            payload_bytes,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE="invalid_hash_signature",
        )
        resp2 = paystack_webhook(req2)
        assert resp2.status_code == 401

        # Case 3: Valid HMAC SHA512 signature
        valid_signature = hmac.new(
            "sk_test_simame_webhook_secret_key".encode("utf-8"),
            payload_bytes,
            hashlib.sha512,
        ).hexdigest()

        req3 = factory.post(
            "/api/v1/payments/webhook/",
            payload_bytes,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=valid_signature,
        )
        resp3 = paystack_webhook(req3)
        # Returns 503 because test-ref does not exist in db, but passes signature authentication!
        assert resp3.status_code in (200, 503)


@pytest.mark.django_db
class TestReconciliationCommand:
    def test_reconcile_payouts_settles_success(self, ready_laundry):
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        payout = SettlementService.build_payout(ready_laundry)
        payout.status = Payout.Status.PROCESSING
        payout.reference = "SIM-RECON-001"
        payout.paystack_transfer_code = "TRF_recon_001"
        payout.save()

        mock_paystack = MagicMock()
        mock_paystack.fetch_transfer.return_value = {
            "status": True,
            "data": {
                "status": "success",
                "transfer_code": "TRF_recon_001",
            },
        }

        with patch("payments.management.commands.reconcile_payouts.PaystackService", return_value=mock_paystack):
            call_command("reconcile_payouts", reference="SIM-RECON-001")

        payout.refresh_from_db()
        assert payout.status == Payout.Status.PAID
        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.PAID


@pytest.mark.django_db
class TestConcurrencyAndFraudControls:
    def test_horizontal_authorization_idor_on_payout_setup(self, ready_laundry):
        """Owner A cannot modify Owner B's payout account."""
        attacker = User.objects.create_user(
            email="attacker@fraud.com",
            phone="+233559999999",
            password="StrongPass123!",
            role=User.Role.OWNER,
        )

        from rest_framework.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied, match="not authorised"):
            RecipientService.setup_payout_account(
                laundry=ready_laundry,
                user=attacker,
                payout_phone="0559999999",
                payout_provider="MTN",
                confirmed=True,
            )

    def test_duplicate_webhook_delivery_idempotency(self, ready_laundry):
        """10 duplicate webhook deliveries produce exactly one transition."""
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        payout = SettlementService.build_payout(ready_laundry)
        payout.status = Payout.Status.PROCESSING
        payout.reference = "SIM-DEDUP-TEST-99"
        payout.save()

        event_data = {
            "data": {
                "reference": "SIM-DEDUP-TEST-99",
                "transfer_code": "TRF_dedup_99",
                "amount": int(payout.amount * 100),
                "status": "success",
            }
        }

        # Deliver 10 times with the same dedup key
        for _ in range(10):
            resp = _handle_transfer_event(None, "transfer.success", event_data, "dedup-transfer-key-99")
            assert resp.status_code == 200

        payout.refresh_from_db()
        assert payout.status == Payout.Status.PAID
        assert OrderSettlement.objects.filter(order=order, status=OrderSettlement.Status.PAID).count() == 1

    def test_single_settlement_per_order_constraint(self, ready_laundry):
        """Database constraint prevents duplicate settlements for the same order."""
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        s1 = SettlementService.record_for_order(order)
        assert s1 is not None

        # Re-running record_for_order safely returns the existing settlement
        s2 = SettlementService.record_for_order(order)
        assert s2.id == s1.id
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_two_workers_claiming_same_settlements(self, ready_laundry):
        """When two processes attempt to build payout from same pending pool, only one succeeds."""
        _, order = _build_order()
        order.laundry = ready_laundry
        order.save()

        FinanceService.freeze_price_breakdown(order)
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order, confirmed=True)

        payout1 = SettlementService.build_payout(ready_laundry)
        assert payout1 is not None

        # Second worker immediately tries to build payout from the same laundry
        payout2 = SettlementService.build_payout(ready_laundry)
        assert payout2 is None  # Nothing pending left to claim!

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_two_laundries_completing_together_never_mix_money(self, auth_owner, ready_laundry):
        """
        order.laundry_id == settlement.laundry_id == payout.laundry_id, always.

        Simulates two laundries finishing orders in the same window (the
        interleaving a real concurrent run would produce) and proves neither
        laundry's amount, settlement, or payout recipient ever lands on the
        other's payout.
        """
        other_owner = User.objects.create_user(
            email="owner_payout_test_2@simame.com",
            phone="+233551057140",
            password="StrongPass123!",
            role=User.Role.OWNER,
        )
        other_laundry = Laundry.objects.create(
            owner=other_owner,
            name="Riverside Cleaners",
            description="Second laundry for isolation testing",
            phone_number="+233551057140",
            address="4 Spintex Rd, Accra",
            city="Accra",
            latitude=Decimal("5.6200"),
            longitude=Decimal("-0.1400"),
            pricing_model=Laundry.PricingModel.BY_ITEM,
            price_range=Laundry.PriceRange.MEDIUM,
            payout_status=Laundry.PayoutStatus.PAYOUT_READY,
            payout_method=Laundry.PayoutMethod.MOBILE_MONEY,
            payout_provider="MTN",
            payout_phone="+233551057140",
            payout_phone_normalized="+233551057140",
            paystack_recipient_code="RCP_riverside_mtn_999",
            payout_confirmed_at=timezone.now(),
            payout_confirmed_by=other_owner,
        )

        customer_a = User.objects.create_user(
            email="customer_isolation_a@simame.com", phone="+233551057141", password="StrongPass123!",
        )
        customer_b = User.objects.create_user(
            email="customer_isolation_b@simame.com", phone="+233551057142", password="StrongPass123!",
        )
        # record_for_order reads total_amount/platform_fee straight off the
        # order row -- no priced items needed to exercise settlement/payout
        # isolation between the two laundries.
        order_a = Order.objects.create(
            user=customer_a, laundry=ready_laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal("40.00"), platform_fee=Decimal("0.00"),
            pickup_address="A", delivery_address="A",
        )
        order_b = Order.objects.create(
            user=customer_b, laundry=other_laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal("70.00"), platform_fee=Decimal("0.00"),
            pickup_address="B", delivery_address="B",
        )

        # Interleaved as a real concurrent pair would land: A records, B
        # records, A releases, B releases, A builds, B builds.
        settlement_a = SettlementService.record_for_order(order_a)
        settlement_b = SettlementService.record_for_order(order_b)
        SettlementService.release_for_order(order_a, confirmed=True)
        SettlementService.release_for_order(order_b, confirmed=True)
        payout_a = SettlementService.build_payout(ready_laundry)
        payout_b = SettlementService.build_payout(other_laundry)

        assert settlement_a.laundry_id == order_a.laundry_id == ready_laundry.id
        assert settlement_b.laundry_id == order_b.laundry_id == other_laundry.id

        assert payout_a.laundry_id == ready_laundry.id
        assert payout_b.laundry_id == other_laundry.id
        assert payout_a.amount == Decimal("40.00")
        assert payout_b.amount == Decimal("70.00")

        # Every settlement swept into each payout belongs to that exact
        # laundry -- never the other one.
        assert set(payout_a.settlements.values_list("laundry_id", flat=True)) == {ready_laundry.id}
        assert set(payout_b.settlements.values_list("laundry_id", flat=True)) == {other_laundry.id}

        recipient_a = PayoutService.check_sendable(payout_a)
        recipient_b = PayoutService.check_sendable(payout_b)
        assert recipient_a == "RCP_apex_mtn_123"
        assert recipient_b == "RCP_riverside_mtn_999"
        assert recipient_a != recipient_b

