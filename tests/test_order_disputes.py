"""
Customer "Report a problem" disputes.

An OPEN dispute must hold the order's settlement against every release path
(dispute-window timer, handover code, customer confirmation, owner complete)
until support resolves it, and resolution must either release the money or
go through the existing refund workflow -- never silently reverse money that
has already left the hold.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from marketplace.models import AuditLog, Notification
from ordering.models import Order
from ordering.services.finance_service import FinanceService
from ordering.services.handover import ensure_handover_code
from ordering.services.order_state_machine import OrderStateMachine
from payments.models import OrderDispute, OrderSettlement, Payment
from payments.services import dispute_service
from payments.services.dispute_service import DisputeError
from payments.services.refund import mark_refund_settled
from payments.services.settlement_service import SettlementService
from users.models import User

from test_payments import _build_order


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _held_delivery(order_status=Order.Status.DELIVERED):
    customer, order = _build_order()
    FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    order.payment_status = Order.PaymentStatus.PAID
    order.status = order_status
    order.save()
    ensure_handover_code(order)
    Payment.objects.create(
        user=customer, order=order, amount=order.total_amount, currency='GHS',
        transaction_reference=f'DSP-{order.id.hex[:10]}', payment_method=Payment.Method.CARD,
        status=Payment.Status.SUCCESS,
    )
    SettlementService.record_for_order(order)
    if order_status in (Order.Status.DELIVERED, Order.Status.COMPLETED):
        SettlementService.release_for_order(order, confirmed=False)
    return customer, order


def _report(customer, order, reason='ITEMS_MISSING'):
    return _client(customer).post(
        reverse('order-report-problem', kwargs={'pk': order.id}),
        {'reason': reason, 'details': 'Two shirts missing'}, format='json',
    )


def _settlement(order):
    return OrderSettlement.objects.get(order=order)


def _staff():
    return User.objects.create_user(
        email='support@example.com', phone='233555990500', password='pass', role='ADMIN', is_staff=True,
    )


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.SETTLEMENT_AUTO_RELEASE_HOURS = 48
    settings.PLATFORM_FEE_RATE = 0.00
    settings.TAX_RATE = 0.00


@pytest.mark.django_db
class TestOpeningADispute:
    def test_dispute_before_release_holds_the_money_past_the_window(self):
        customer, order = _held_delivery()

        response = _report(customer, order)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()['data']
        assert body['order']['dispute']['status'] == 'OPEN'
        assert body['order']['can_report_problem'] is False
        assert body['order']['can_confirm_received'] is False
        # The window expires; the timer must not pay the laundry.
        assert SettlementService.run_auto_release(now=timezone.now() + timedelta(hours=49)) == 0
        assert _settlement(order).status == OrderSettlement.Status.HELD
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_another_customer_cannot_dispute_this_order(self):
        _, order = _held_delivery()
        intruder = User.objects.create_user(email='intruder-d@example.com', phone='233555990501', password='pass')

        response = _report(intruder, order)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not OrderDispute.objects.filter(order=order).exists()

    def test_the_laundry_owner_cannot_open_a_dispute_on_the_order(self):
        _, order = _held_delivery()

        response = _report(order.laundry.owner, order)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not OrderDispute.objects.filter(order=order).exists()

    def test_service_backstop_refuses_a_non_owning_customer(self):
        _, order = _held_delivery()
        intruder = User.objects.create_user(email='intruder-s@example.com', phone='233555990502', password='pass')

        with pytest.raises(DisputeError):
            dispute_service.open_dispute(order, intruder, 'OTHER')

    def test_anonymous_request_is_rejected(self):
        _, order = _held_delivery()
        response = APIClient().post(reverse('order-report-problem', kwargs={'pk': order.id}), {'reason': 'OTHER'})
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_duplicate_reports_create_one_dispute_and_one_set_of_messages(self, django_capture_on_commit_callbacks):
        customer, order = _held_delivery()

        with django_capture_on_commit_callbacks(execute=True):
            first = _report(customer, order)
        with django_capture_on_commit_callbacks(execute=True):
            second = _report(customer, order, reason='ITEMS_DAMAGED')

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_200_OK
        assert 'already reported' in second.json()['message']
        assert OrderDispute.objects.filter(order=order).count() == 1
        assert AuditLog.objects.filter(action='ORDER_DISPUTE_OPENED', target_type='OrderDispute').count() == 1
        assert Notification.objects.filter(user=customer, category='ORDER_DISPUTE').count() == 1
        assert Notification.objects.filter(user=order.laundry.owner, category='ORDER_DISPUTE').count() == 1

    def test_customer_and_laundry_are_told_simply(self, django_capture_on_commit_callbacks):
        customer, order = _held_delivery()
        with django_capture_on_commit_callbacks(execute=True):
            _report(customer, order)

        customer_note = Notification.objects.get(user=customer, category='ORDER_DISPUTE')
        owner_note = Notification.objects.get(user=order.laundry.owner, category='ORDER_DISPUTE')
        assert 'on hold' in customer_note.body
        assert 'on hold' in owner_note.body
        assert AuditLog.objects.filter(action='ORDER_DISPUTE_OPENED').count() == 1

    def test_invalid_reason_is_rejected(self):
        customer, order = _held_delivery()
        response = _report(customer, order, reason='BOGUS')
        assert response.status_code == status.HTTP_409_CONFLICT
        assert not OrderDispute.objects.exists()

    def test_too_early_is_rejected(self):
        customer, order = _held_delivery(order_status=Order.Status.IN_PROCESS)
        response = _report(customer, order)
        assert response.status_code == status.HTTP_409_CONFLICT
        assert not OrderDispute.objects.exists()

    def test_already_released_money_cannot_be_disputed_through_the_app(self):
        customer, order = _held_delivery()
        SettlementService.release_for_order(order, confirmed=True)
        assert _settlement(order).status == OrderSettlement.Status.PENDING

        response = _report(customer, order)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert 'contact support' in response.json()['message']
        assert not OrderDispute.objects.exists()
        assert _settlement(order).status == OrderSettlement.Status.PENDING


@pytest.mark.django_db
class TestTheHoldCannotBeBypassed:
    def test_customer_confirmation_is_refused_while_their_dispute_is_open(self):
        customer, order = _held_delivery()
        _report(customer, order)

        response = _client(customer).post(reverse('order-confirm-received', kwargs={'pk': order.id}), {}, format='json')

        assert response.status_code == status.HTTP_409_CONFLICT
        assert _settlement(order).status == OrderSettlement.Status.HELD

    def test_owner_entering_the_correct_code_does_not_release_a_disputed_order(self):
        customer, order = _held_delivery(order_status=Order.Status.OUT_FOR_DELIVERY)
        _report(customer, order)

        response = _client(order.laundry.owner).patch(
            reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id}),
            {'handover_code': order.handover_code}, format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        assert _settlement(order).status == OrderSettlement.Status.HELD
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_owner_completing_does_not_release_a_disputed_order(self):
        customer, order = _held_delivery()
        _report(customer, order)

        _client(order.laundry.owner).patch(reverse('order-lifecycle-complete', kwargs={'pk': order.id}), {}, format='json')

        order.refresh_from_db()
        assert order.status == Order.Status.COMPLETED
        assert _settlement(order).status == OrderSettlement.Status.HELD

    def test_owner_cannot_resolve_a_dispute(self):
        customer, order = _held_delivery()
        dispute, _ = dispute_service.open_dispute(order, customer, 'ITEMS_MISSING')

        with pytest.raises(DisputeError):
            dispute_service.resolve_release(dispute, order.laundry.owner)
        with pytest.raises(DisputeError):
            dispute_service.resolve_refund(dispute, order.laundry.owner)

        dispute.refresh_from_db()
        assert dispute.status == OrderDispute.Status.OPEN
        assert _settlement(order).status == OrderSettlement.Status.HELD

    def test_owner_dashboard_shows_the_hold_read_only(self):
        customer, order = _held_delivery()
        _report(customer, order)

        response = _client(order.laundry.owner).get(reverse('dashboard-orders-detail', kwargs={'pk': order.id}))

        assert response.status_code == status.HTTP_200_OK
        payload = response.json().get('data', response.json())
        assert payload['dispute_status'] == 'OPEN'


@pytest.mark.django_db
class TestResolution:
    def test_resolution_to_release_pays_the_laundry_once(self, django_capture_on_commit_callbacks):
        customer, order = _held_delivery()
        dispute, _ = dispute_service.open_dispute(order, customer, 'ITEMS_MISSING')
        staff = _staff()

        with django_capture_on_commit_callbacks(execute=True):
            resolved, changed = dispute_service.resolve_release(dispute, staff, note='Items found')
        again, changed_again = dispute_service.resolve_release(dispute, staff)

        assert changed is True and changed_again is False
        assert resolved.status == OrderDispute.Status.RESOLVED_RELEASED
        assert resolved.resolved_by == staff
        assert _settlement(order).status == OrderSettlement.Status.PENDING
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount
        assert AuditLog.objects.filter(action='ORDER_DISPUTE_RESOLVED').count() == 1
        assert Notification.objects.filter(
            user=order.laundry.owner, category='ORDER_DISPUTE', body__icontains='released'
        ).exists()

    def test_resolution_to_refund_goes_through_the_refund_workflow(self):
        customer, order = _held_delivery()
        dispute, _ = dispute_service.open_dispute(order, customer, 'NOT_RECEIVED')
        staff = _staff()

        with patch('payments.services.refund.PaystackService') as paystack:
            paystack.return_value.refund_transaction.return_value = {'status': True, 'data': {}}
            resolved, _ = dispute_service.resolve_refund(dispute, staff, note='Not delivered')
            dispute_service.resolve_refund(dispute, staff)  # repeat: no second refund

        assert paystack.return_value.refund_transaction.call_count == 1
        assert resolved.status == OrderDispute.Status.RESOLVED_REFUNDED
        payment = Payment.objects.get(order=order)
        assert payment.status == Payment.Status.REFUND_PENDING
        # The dispute is closed, but the refund in flight still holds the money.
        assert SettlementService.run_auto_release(now=timezone.now() + timedelta(hours=49)) == 0
        assert _settlement(order).status == OrderSettlement.Status.HELD

        mark_refund_settled(payment)

        assert _settlement(order).status == OrderSettlement.Status.REVERSED
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_a_rejected_refund_leaves_the_dispute_open_and_the_money_held(self):
        customer, order = _held_delivery()
        dispute, _ = dispute_service.open_dispute(order, customer, 'NOT_RECEIVED')

        with patch('payments.services.refund.PaystackService') as paystack:
            paystack.return_value.refund_transaction.return_value = {'status': False, 'message': 'Declined'}
            with pytest.raises(DisputeError):
                dispute_service.resolve_refund(dispute, _staff())

        dispute.refresh_from_db()
        assert dispute.status == OrderDispute.Status.OPEN
        assert _settlement(order).status == OrderSettlement.Status.HELD

    def test_already_paid_money_goes_to_manual_review_not_a_silent_reversal(self, django_capture_on_commit_callbacks):
        customer, order = _held_delivery()
        dispute, _ = dispute_service.open_dispute(order, customer, 'ITEMS_DAMAGED')
        # Simulate money having left the hold by some out-of-band path.
        OrderSettlement.objects.filter(order=order).update(status=OrderSettlement.Status.PAID)

        staff = _staff()
        with patch('payments.services.refund.PaystackService') as paystack,                 django_capture_on_commit_callbacks(execute=True):
            resolved, _ = dispute_service.resolve_refund(dispute, staff, note='Damaged')

        assert paystack.return_value.refund_transaction.call_count == 0
        assert resolved.status == OrderDispute.Status.MANUAL_REVIEW
        assert _settlement(order).status == OrderSettlement.Status.PAID
        assert Payment.objects.get(order=order).status == Payment.Status.SUCCESS
        assert Notification.objects.filter(
            audience=Notification.Audience.ADMIN, title__icontains='manual financial review'
        ).exists()
