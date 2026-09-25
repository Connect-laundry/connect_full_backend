"""
A handover code must only ever work when a delivery is actually expected.

Submitting the *correct* code against an order that isn't OUT_FOR_DELIVERY
must be rejected outright, and -- this is the part that actually matters --
must not leave any side effect behind. `mark_delivered` used to verify and
record the code before checking whether DELIVERED was even a reachable
state, so a correct code against a CANCELLED/REJECTED/PENDING order set
`delivery_confirmed_by_code=True` and `delivered_at` as a side effect even
though the request came back as a 400.
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

from test_payments import _build_order


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _order_in_state(order_status):
    _, order = _build_order()
    with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
        FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    ensure_handover_code(order)
    order.status = order_status
    order.save()
    SettlementService.record_for_order(order)
    return order


@pytest.mark.django_db
class TestHandoverCodeOrderStateRules:
    @pytest.fixture(autouse=True)
    def _fresh_counters(self):
        cache.clear()
        yield
        cache.clear()

    @pytest.mark.parametrize("blocked_status", [
        Order.Status.PENDING,
        Order.Status.CONFIRMED,
        Order.Status.PICKED_UP,
        Order.Status.IN_PROCESS,
        Order.Status.CANCELLED,
        Order.Status.REJECTED,
        Order.Status.COMPLETED,
    ])
    def test_the_correct_code_is_rejected_outside_out_for_delivery(self, blocked_status):
        order = _order_in_state(blocked_status)
        client = _auth_client(order.laundry.owner)
        url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})

        response = client.patch(url, {'handover_code': order.handover_code}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        order.refresh_from_db()
        # The rejected request must leave no trace: no confirmation flag, no
        # delivered_at timestamp, no status change, no released money.
        assert order.status == blocked_status
        assert order.delivery_confirmed_by_code is False
        assert order.delivered_at is None
        if blocked_status != Order.Status.COMPLETED:
            assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')
