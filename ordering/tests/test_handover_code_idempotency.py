"""
Repeat submissions of an already-consumed handover code must never release
the escrow, build a payout, or write duplicate history a second time.

Covers: double-click, a retried request after a dropped response, and a
second (right or wrong) submission after the order has moved past DELIVERED.
True concurrent-thread proof needs Postgres row locking (SQLite --
this project's test DB -- treats select_for_update as a no-op and would not
faithfully represent production); the code path itself is reviewed in
SIMAME_DELIVERY_CONFIRMATION_CERTIFICATION.md.
"""
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from ordering.models import Order
from ordering.models.base import OrderStatusHistory
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
class TestHandoverCodeIdempotency:
    @pytest.fixture(autouse=True)
    def _fresh_counters(self):
        cache.clear()
        yield
        cache.clear()

    def test_submitting_the_correct_code_twice_releases_only_once(self):
        owner, order = _out_for_delivery_order()
        client = _auth_client(owner)
        url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})

        first = client.patch(url, {'handover_code': order.handover_code}, format='json')
        second = client.patch(url, {'handover_code': order.handover_code}, format='json')

        # DELIVERED->DELIVERED is a deliberate no-op success in
        # OrderStateMachine.transition (`from_status == to_status`), so a
        # retried request after a dropped response reads as "already done",
        # not an error -- but it must not re-run release/history/payout.
        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK

        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount
        # Exactly one DELIVERED entry in the audit trail, not two.
        delivered_entries = OrderStatusHistory.objects.filter(
            order=order, new_status=Order.Status.DELIVERED
        ).count()
        assert delivered_entries == 1

    def test_wrong_code_after_confirmation_cannot_undo_or_duplicate_anything(self):
        owner, order = _out_for_delivery_order()
        client = _auth_client(owner)
        url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})

        client.patch(url, {'handover_code': order.handover_code}, format='json')
        wrong = '9999' if order.handover_code != '9999' else '1234'
        replay = client.patch(url, {'handover_code': wrong}, format='json')

        assert replay.status_code == status.HTTP_400_BAD_REQUEST
        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED
        assert order.delivery_confirmed_by_code is True
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_completing_after_confirmed_delivery_does_not_double_payout(self):
        owner, order = _out_for_delivery_order()
        client = _auth_client(owner)
        deliver_url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})
        complete_url = reverse('order-lifecycle-complete', kwargs={'pk': order.id})

        client.patch(deliver_url, {'handover_code': order.handover_code}, format='json')
        first_complete = client.patch(complete_url, {}, format='json')
        second_complete = client.patch(complete_url, {}, format='json')

        # COMPLETED->COMPLETED is the same deliberate no-op success path.
        assert first_complete.status_code == status.HTTP_200_OK
        assert second_complete.status_code == status.HTTP_200_OK

        order.refresh_from_db()
        assert order.status == Order.Status.COMPLETED
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount
        completed_entries = OrderStatusHistory.objects.filter(
            order=order, new_status=Order.Status.COMPLETED
        ).count()
        assert completed_entries == 1
