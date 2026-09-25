"""
Brute-force protection on handover-code verification.

A handover code is 4 digits (10,000 possibilities). Without a scoped limit,
the laundry owner's generous general API budget (120/min) would let a rogue
owner exhaust the whole space against one customer's order in under 90
minutes and falsely claim delivery. `HANDOVER_CODE_THROTTLES`
(config/throttling.py) closes that: 5 attempts per order per 10 minutes.
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


def _out_for_delivery_order():
    _, order = _build_order()
    with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
        FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    order.payment_status = Order.PaymentStatus.PAID
    order.status = Order.Status.OUT_FOR_DELIVERY
    order.save()
    ensure_handover_code(order)
    SettlementService.record_for_order(order)
    return order.laundry.owner, order


@pytest.mark.django_db
class TestHandoverCodeBruteForceProtection:
    @pytest.fixture(autouse=True)
    def _fresh_counters(self):
        cache.clear()
        yield
        cache.clear()

    @override_settings(HANDOVER_CODE_ORDER_RATE='5/10m')
    def test_repeated_wrong_codes_are_throttled_per_order(self):
        owner, order = _out_for_delivery_order()
        # The real code is unlikely to be any of these; guarantee it.
        wrong = '9999' if order.handover_code != '9999' else '1234'
        client = _auth_client(owner)
        url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})

        results = [
            client.patch(url, {'handover_code': wrong}, format='json').status_code
            for _ in range(8)
        ]

        assert results.count(status.HTTP_429_TOO_MANY_REQUESTS) >= 3
        # None of the guesses were ever right, so the order must still be
        # sitting exactly where it started.
        order.refresh_from_db()
        assert order.status == Order.Status.OUT_FOR_DELIVERY
        assert order.delivery_confirmed_by_code is False

    @override_settings(HANDOVER_CODE_ORDER_RATE='5/10m')
    def test_throttle_does_not_block_the_first_correct_attempt(self):
        owner, order = _out_for_delivery_order()
        client = _auth_client(owner)
        url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})

        response = client.patch(url, {'handover_code': order.handover_code}, format='json')

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED
        assert order.delivery_confirmed_by_code is True
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    @override_settings(HANDOVER_CODE_ORDER_RATE='5/10m', HANDOVER_CODE_OWNER_RATE='1000/h')
    def test_brute_force_across_the_whole_code_space_is_infeasible(self):
        # 5 attempts / 10 minutes means exhausting the 10,000-code space takes
        # roughly 2000 windows -- weeks, not minutes. Confirm the ceiling is
        # exactly the configured 5, not something silently more permissive.
        owner, order = _out_for_delivery_order()
        wrong = '9999' if order.handover_code != '9999' else '1234'
        client = _auth_client(owner)
        url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})

        results = [
            client.patch(url, {'handover_code': wrong}, format='json').status_code
            for _ in range(20)
        ]

        assert results.count(status.HTTP_400_BAD_REQUEST) == 5
        assert results.count(status.HTTP_429_TOO_MANY_REQUESTS) == 15
