"""
POST /orders/{id}/confirm-received/ -- the customer's one-tap alternative to
reading the handover code out to the laundry.

Today neither production frontend actually lets a laundry type in the
customer's code (the owner web app's `mark-delivered` call sends no body at
all), so without this endpoint every single delivery falls through to the
unproved path and just waits out the full dispute window. This gives the
customer, who already sees the code in the app, a real way to trigger the
same trusted confirmation themselves.
"""
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from ordering.models import Order
from ordering.services.finance_service import FinanceService
from ordering.services.handover import ensure_handover_code
from payments.services.settlement_service import SettlementService
from users.models import User

from test_payments import _build_order


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _paid_order(status_value):
    customer, order = _build_order()
    with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
        FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    order.payment_status = Order.PaymentStatus.PAID
    order.status = status_value
    order.save()
    ensure_handover_code(order)
    SettlementService.record_for_order(order)
    return customer, order


@pytest.mark.django_db
class TestCustomerConfirmReceived:
    @pytest.fixture(autouse=True)
    def _fresh_counters(self):
        cache.clear()
        yield
        cache.clear()

    def _url(self, order):
        return reverse('order-confirm-received', kwargs={'pk': order.id})

    def test_customer_confirms_out_for_delivery_order(self):
        customer, order = _paid_order(Order.Status.OUT_FOR_DELIVERY)
        client = _auth_client(customer)

        response = client.post(self._url(order), {}, format='json')

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED
        assert order.delivery_confirmed_by_code is True
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_customer_confirms_an_owner_marked_delivered_order_with_no_proof(self):
        # Owner already marked DELIVERED with no code (money HELD behind the
        # dispute-window timer). The customer's own tap releases it early.
        customer, order = _paid_order(Order.Status.DELIVERED)
        client = _auth_client(customer)

        response = client.post(self._url(order), {}, format='json')

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED  # unchanged, no transition needed
        assert order.delivery_confirmed_by_code is True
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_customer_confirms_after_owner_completed_without_proof(self):
        # The P0 owner-completion bypass is closed: COMPLETED alone no longer
        # releases money. The customer confirming afterwards still can.
        customer, order = _paid_order(Order.Status.DELIVERED)
        order.status = Order.Status.COMPLETED
        order.save()
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')
        client = _auth_client(customer)

        response = client.post(self._url(order), {}, format='json')

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.delivery_confirmed_by_code is True
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_confirming_twice_is_idempotent(self):
        customer, order = _paid_order(Order.Status.OUT_FOR_DELIVERY)
        client = _auth_client(customer)

        first = client.post(self._url(order), {}, format='json')
        second = client.post(self._url(order), {}, format='json')

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert 'already confirmed' in second.json()['message'].lower()
        order.refresh_from_db()
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_too_early_is_rejected(self):
        customer, order = _paid_order(Order.Status.IN_PROCESS)
        client = _auth_client(customer)

        response = client.post(self._url(order), {}, format='json')

        assert response.status_code == status.HTTP_409_CONFLICT
        order.refresh_from_db()
        assert order.delivery_confirmed_by_code is False

    def test_another_customer_cannot_confirm_this_order(self):
        customer, order = _paid_order(Order.Status.OUT_FOR_DELIVERY)
        intruder = User.objects.create_user(
            email='confirm-intruder@example.com', phone='233555990098', password='pass',
        )
        client = _auth_client(intruder)

        response = client.post(self._url(order), {}, format='json')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        order.refresh_from_db()
        assert order.delivery_confirmed_by_code is False

    def test_the_laundry_owner_cannot_confirm_their_own_order(self):
        # Confirmation must come from the customer's own account -- an owner
        # confirming their own delivery would be exactly the bypass this
        # whole flow exists to prevent.
        customer, order = _paid_order(Order.Status.OUT_FOR_DELIVERY)
        owner = order.laundry.owner
        client = _auth_client(owner)

        response = client.post(self._url(order), {}, format='json')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        order.refresh_from_db()
        assert order.delivery_confirmed_by_code is False
