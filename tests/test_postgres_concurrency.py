"""
Real concurrency proof for the Model A money path.

Runs only against PostgreSQL. SQLite has no row-level locking, so
`select_for_update()` is a no-op there and a threaded test would prove
nothing. Each case starts N real threads behind a barrier, each with its own
database connection, through the real HTTP endpoints and services, with
real commits (transaction=True). Only Paystack is replaced, by a thread-safe
fake that records every transfer it is asked to make.

Run with a Postgres settings module, e.g.:
    pytest --ds=<postgres settings> tests/test_postgres_concurrency.py
"""
import itertools
import threading
import time
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from laundries.models.laundry import Laundry
from ordering.models import Order
from ordering.models.base import OrderStatusHistory
from ordering.services.handover import ensure_handover_code
from payments.models import OrderSettlement, Payment, Payout
from payments.services.payout_service import PayoutError, PayoutService
from payments.services.settlement_service import SettlementService
from users.models import User

pytestmark = [
    pytest.mark.skipif(connection.vendor != 'postgresql', reason='needs PostgreSQL row locking'),
    pytest.mark.django_db(transaction=True),
]

N = 20
_seq = itertools.count(1)


class FakePaystack:
    """Records every transfer; sleeps briefly to widen any race window."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []

    def initiate_transfer(self, amount, recipient_code, reference, reason=''):
        with self.lock:
            self.calls.append({'amount': Decimal(str(amount)), 'recipient': recipient_code, 'reference': reference})
        time.sleep(0.05)
        return {'status': True, 'data': {'transfer_code': f'TRF_{reference}'}}


@pytest.fixture(autouse=True)
def _money_settings(settings):
    settings.PAYSTACK_TRANSFERS_ENABLED = True
    settings.PAYOUT_AUTOMATIC_ENABLED = True
    settings.PAYOUT_MINIMUM_AMOUNT = '1.00'
    settings.PAYOUT_MAX_TRANSFER_AMOUNT = '5000.00'
    settings.SETTLEMENT_AUTO_RELEASE_HOURS = 48
    # Let every concurrent attempt reach the database; throttling is proven
    # separately and would otherwise hide the race being tested here.
    rates = dict(SimpleRateThrottle.THROTTLE_RATES)
    rates.update({'handover_code_order': '1000/m', 'handover_code_owner': '1000/m',
                  'order_dispute': '1000/m',
                  'burst_user': '10000/m', 'sustained_user': '100000/d'})
    with patch.object(SimpleRateThrottle, 'THROTTLE_RATES', rates):
        yield


@pytest.fixture
def paystack():
    fake = FakePaystack()
    with patch('payments.services.paystack.PaystackService', lambda *a, **k: fake):
        yield fake


@pytest.fixture
def released_events():
    events = []
    lock = threading.Lock()

    def record(settlement):
        with lock:
            events.append(settlement.id)

    with patch('payments.services.payout_notifications.earnings_available', record):
        yield events


def _run_concurrently(n, fn):
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            barrier.wait(timeout=30)
            results[i] = fn(i)
        except Exception as exc:  # recorded, asserted on by the caller
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), 'a worker hung'
    return results


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _laundry():
    n = next(_seq)
    owner = User.objects.create_user(
        email=f'pg-owner-{n}@example.com', phone=f'23357{n:07d}',
        password='StrongPass123!', role=User.Role.OWNER,
    )
    return Laundry.objects.create(
        owner=owner, name=f'PG Laundry {n}', description='concurrency', address='Accra',
        city='Accra', latitude=Decimal('5.6'), longitude=Decimal('-0.18'),
        phone_number=f'024{n:07d}', status=Laundry.ApprovalStatus.APPROVED, is_active=True,
        payout_status=Laundry.PayoutStatus.PAYOUT_READY,
        payout_method=Laundry.PayoutMethod.MOBILE_MONEY, payout_provider='MTN',
        payout_phone=f'+23355{n:07d}', payout_phone_normalized=f'+23355{n:07d}',
        paystack_recipient_code=f'RCP_pg_{n}', payout_confirmed_at=timezone.now(),
        payout_confirmed_by=owner,
    )


def _paid_order(laundry, amount, status):
    n = next(_seq)
    customer = User.objects.create_user(
        email=f'pg-customer-{n}@example.com', phone=f'23358{n:07d}', password='StrongPass123!',
    )
    order = Order.objects.create(
        user=customer, laundry=laundry, pickup_date=timezone.now(),
        total_amount=Decimal(amount), platform_fee=Decimal('0.00'),
        pickup_address='A', delivery_address='A',
        payment_status=Order.PaymentStatus.PAID, status=status,
    )
    Payment.objects.create(
        user=customer, order=order, amount=order.total_amount, currency='GHS',
        transaction_reference=f'PG-{order.id.hex[:12]}', payment_method=Payment.Method.CARD,
        status=Payment.Status.SUCCESS,
    )
    ensure_handover_code(order)
    SettlementService.record_for_order(order)
    return order


def _history_count(order, status):
    return OrderStatusHistory.objects.filter(order=order, new_status=status).count()


# -- A. Same order ------------------------------------------------------------

def test_a_20_simultaneous_correct_codes_confirm_once(paystack, released_events):
    laundry = _laundry()
    order = _paid_order(laundry, '40.00', Order.Status.OUT_FOR_DELIVERY)
    url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})

    results = _run_concurrently(
        N, lambda i: _client(laundry.owner).patch(url, {'handover_code': order.handover_code}, format='json').status_code
    )

    assert all(r == 200 for r in results), results
    order.refresh_from_db()
    settlement = OrderSettlement.objects.get(order=order)
    assert order.status == Order.Status.DELIVERED
    assert order.delivery_confirmed_by_code is True
    assert _history_count(order, Order.Status.DELIVERED) == 1
    assert settlement.status == OrderSettlement.Status.PENDING
    assert released_events == [settlement.id]
    assert SettlementService.outstanding_total(laundry) == Decimal('40.00')
    assert paystack.calls == []  # nothing is paid until COMPLETED


def test_a_20_simultaneous_customer_confirmations_confirm_once(paystack, released_events):
    laundry = _laundry()
    order = _paid_order(laundry, '35.00', Order.Status.OUT_FOR_DELIVERY)
    url = reverse('order-confirm-received', kwargs={'pk': order.id})

    results = _run_concurrently(N, lambda i: _client(order.user).post(url, {}, format='json').status_code)

    assert all(r == 200 for r in results), results
    order.refresh_from_db()
    settlement = OrderSettlement.objects.get(order=order)
    assert order.status == Order.Status.DELIVERED
    assert _history_count(order, Order.Status.DELIVERED) == 1
    assert settlement.status == OrderSettlement.Status.PENDING
    assert released_events == [settlement.id]


# -- B. Same owner: Delivered and Complete racing each other ------------------

def test_b_20_simultaneous_delivered_and_complete_requests_pay_once(paystack, released_events):
    laundry = _laundry()
    order = _paid_order(laundry, '55.00', Order.Status.OUT_FOR_DELIVERY)
    deliver = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})
    complete = reverse('order-lifecycle-complete', kwargs={'pk': order.id})

    def hit(i):
        c = _client(laundry.owner)
        if i % 2 == 0:
            return c.patch(deliver, {'handover_code': order.handover_code}, format='json').status_code
        return c.patch(complete, {}, format='json').status_code

    _run_concurrently(N, hit)
    # Whatever interleaving happened, finish the lifecycle once more so the
    # end state is deterministic, then prove nothing was duplicated.
    _client(laundry.owner).patch(complete, {}, format='json')

    order.refresh_from_db()
    settlement = OrderSettlement.objects.get(order=order)
    assert order.status == Order.Status.COMPLETED
    assert _history_count(order, Order.Status.DELIVERED) == 1
    assert _history_count(order, Order.Status.COMPLETED) == 1
    assert released_events == [settlement.id]
    payouts = list(Payout.objects.filter(laundry=laundry))
    assert len(payouts) == 1
    assert payouts[0].amount == Decimal('55.00')
    assert len(paystack.calls) == 1
    assert paystack.calls[0]['amount'] == Decimal('55.00')
    assert paystack.calls[0]['recipient'] == laundry.paystack_recipient_code
    assert settlement.status == OrderSettlement.Status.SCHEDULED
    assert settlement.payout_id == payouts[0].id


# -- C. Twenty laundries completing at once -------------------------------------

def test_c_20_laundries_completing_simultaneously_never_mix_money(paystack):
    laundries = [_laundry() for _ in range(N)]
    orders = []
    for i, laundry in enumerate(laundries):
        order = _paid_order(laundry, f'{10 + i}.{i:02d}', Order.Status.OUT_FOR_DELIVERY)
        _client(laundry.owner).patch(
            reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id}),
            {'handover_code': order.handover_code}, format='json',
        )
        orders.append(order)

    results = _run_concurrently(
        N,
        lambda i: _client(laundries[i].owner).patch(
            reverse('order-lifecycle-complete', kwargs={'pk': orders[i].id}), {}, format='json'
        ).status_code,
    )

    assert all(r == 200 for r in results), results
    assert len(paystack.calls) == N
    calls_by_ref = {c['reference']: c for c in paystack.calls}
    assert len(calls_by_ref) == N  # every transfer reference unique

    for laundry, order in zip(laundries, orders):
        settlement = OrderSettlement.objects.get(order=order)
        payout = Payout.objects.get(laundry=laundry)
        call = calls_by_ref[payout.reference]
        assert order.laundry_id == settlement.laundry_id == payout.laundry_id == laundry.id
        assert settlement.payout_id == payout.id
        assert set(payout.settlements.values_list('laundry_id', flat=True)) == {laundry.id}
        assert payout.amount == order.total_amount == settlement.net_payable
        assert payout.recipient_code_used == laundry.paystack_recipient_code
        assert call['recipient'] == laundry.paystack_recipient_code
        assert call['amount'] == order.total_amount


# -- D. Payout workers racing on the same payout -------------------------------

def test_d_20_payout_workers_initiate_exactly_one_transfer(paystack):
    laundry = _laundry()
    order = _paid_order(laundry, '75.00', Order.Status.DELIVERED)
    SettlementService.release_for_order(order, confirmed=True)
    payout = SettlementService.build_payout(laundry, method=Payout.Method.PAYSTACK)

    def send(i):
        try:
            return PayoutService.send(Payout.objects.get(pk=payout.pk))
        except PayoutError as exc:
            return exc

    results = _run_concurrently(N, send)

    sent = [r for r in results if isinstance(r, Payout)]
    refused = [r for r in results if isinstance(r, PayoutError)]
    assert len(sent) == 1
    assert len(refused) == N - 1
    assert len(paystack.calls) == 1
    payout.refresh_from_db()
    assert payout.status == Payout.Status.PROCESSING
    assert paystack.calls[0]['recipient'] == laundry.paystack_recipient_code == payout.recipient_code_used


# -- E. Dispute-window auto-release racing across workers -----------------------

def test_e_concurrent_auto_release_workers_release_each_settlement_once(paystack, released_events):
    laundries = [_laundry() for _ in range(N)]
    orders = []
    for i, laundry in enumerate(laundries):
        order = _paid_order(laundry, f'{20 + i}.00', Order.Status.DELIVERED)
        SettlementService.release_for_order(order, confirmed=False)
        orders.append(order)
    # One of them has a refund in flight: the timer must not pay it.
    refunding = orders[0]
    Payment.objects.filter(order=refunding).update(status=Payment.Status.REFUND_PENDING)
    OrderSettlement.objects.update(release_after=timezone.now() - timezone.timedelta(minutes=1))

    results = _run_concurrently(5, lambda i: SettlementService.run_auto_release())

    assert all(isinstance(r, int) for r in results), results
    assert sum(results) == N - 1
    assert sorted(released_events) == sorted(
        OrderSettlement.objects.exclude(order=refunding).values_list('id', flat=True)
    )
    assert OrderSettlement.objects.get(order=refunding).status == OrderSettlement.Status.HELD
    assert not Payout.objects.filter(laundry=refunding.laundry).exists()
    assert len(paystack.calls) == N - 1
    for laundry, order in zip(laundries[1:], orders[1:]):
        payout = Payout.objects.get(laundry=laundry)
        assert payout.amount == order.total_amount
        assert payout.recipient_code_used == laundry.paystack_recipient_code


# -- F. Duplicate dispute reports ------------------------------------------------

def test_f_20_simultaneous_reports_open_exactly_one_dispute():
    from marketplace.models import AuditLog
    from payments.models import OrderDispute

    laundry = _laundry()
    order = _paid_order(laundry, '30.00', Order.Status.DELIVERED)
    SettlementService.release_for_order(order, confirmed=False)
    url = reverse('order-report-problem', kwargs={'pk': order.id})

    results = _run_concurrently(
        N, lambda i: _client(order.user).post(url, {'reason': 'ITEMS_MISSING'}, format='json').status_code
    )

    assert sorted(results) == [200] * (N - 1) + [201], results
    assert OrderDispute.objects.filter(order=order).count() == 1
    assert AuditLog.objects.filter(action='ORDER_DISPUTE_OPENED').count() == 1
    assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.HELD


# -- G. Dispute racing release, symmetric per-order race -----------------------

def _due_disputable_orders(n):
    laundries = [_laundry() for _ in range(n)]
    orders = []
    for i, laundry in enumerate(laundries):
        order = _paid_order(laundry, f'{40 + i}.00', Order.Status.DELIVERED)
        SettlementService.release_for_order(order, confirmed=False)
        orders.append(order)
    OrderSettlement.objects.update(release_after=timezone.now() - timezone.timedelta(minutes=1))
    return orders


def _assert_dispute_release_invariant(orders):
    """Every order ends in exactly one coherent state; returns (#held, #released)."""
    from payments.models import OrderDispute

    held = released = 0
    for order in orders:
        settlement = OrderSettlement.objects.get(order=order)
        open_disputes = OrderDispute.objects.filter(order=order, status=OrderDispute.Status.OPEN).count()
        assert open_disputes <= 1
        if open_disputes:
            held += 1
            assert settlement.status == OrderSettlement.Status.HELD, 'disputed order was released'
            assert not Payout.objects.filter(laundry=order.laundry).exists()
        else:
            released += 1
            assert settlement.status != OrderSettlement.Status.HELD, 'undisputed order left held'
    return held, released


def test_g_disputes_racing_releases_per_order(paystack):
    from payments.services.dispute_service import DisputeError, open_dispute

    orders = _due_disputable_orders(N)

    def act(i):
        order = orders[i // 2]
        if i % 2 == 0:
            try:
                return open_dispute(order, order.user, 'NOT_RECEIVED')[1]
            except DisputeError:
                return 'refused'
        return SettlementService.release_for_order(order, confirmed=True) is not None

    results = _run_concurrently(2 * N, act)

    assert all(not isinstance(r, Exception) for r in results), results
    held, released = _assert_dispute_release_invariant(orders)
    print(f'race outcome: {held} held by dispute, {released} released first')
    assert held == sum(1 for r in results[0::2] if r is True)
    assert released == sum(1 for r in results[1::2] if r is True)
    assert paystack.calls == []  # release_for_order does not itself transfer


# -- H. Both lock orderings, forced deterministically ---------------------------

def test_h_dispute_holding_the_lock_blocks_a_concurrent_timer_release(paystack):
    """Dispute transaction holds the settlement lock; the timer waits, then must not release."""
    from payments.models import OrderDispute
    from payments.services import dispute_service

    (order,) = _due_disputable_orders(1)
    original_audit = dispute_service.record_audit

    def slow_audit(**kwargs):
        time.sleep(0.8)  # inside open_dispute's transaction, locks held
        return original_audit(**kwargs)

    def act(i):
        if i == 0:
            return dispute_service.open_dispute(order, order.user, 'ITEMS_MISSING')[1]
        time.sleep(0.2)  # let the dispute take the lock first
        return SettlementService.run_auto_release()

    with patch.object(dispute_service, 'record_audit', slow_audit):
        results = _run_concurrently(2, act)

    assert results == [True, 0], results
    assert OrderDispute.objects.filter(order=order, status='OPEN').count() == 1
    assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.HELD
    assert paystack.calls == []


def test_h_release_holding_the_lock_makes_a_concurrent_dispute_refuse(paystack):
    """Timer transaction holds the settlement lock; the dispute waits, then must be refused."""
    from payments.models import OrderDispute
    from payments.services import settlement_service
    from payments.services.dispute_service import DisputeError, open_dispute

    (order,) = _due_disputable_orders(1)
    original_blocker = settlement_service.release_blocker

    def slow_blocker(settlement):
        result = original_blocker(settlement)
        time.sleep(0.8)  # inside run_auto_release's per-row transaction, lock held
        return result

    def act(i):
        if i == 0:
            return SettlementService.run_auto_release()
        time.sleep(0.2)  # let the timer take the lock first
        try:
            return open_dispute(order, order.user, 'ITEMS_MISSING')[1]
        except DisputeError:
            return 'refused'

    with patch.object(settlement_service, 'release_blocker', slow_blocker):
        results = _run_concurrently(2, act)

    assert results == [1, 'refused'], results
    assert not OrderDispute.objects.filter(order=order).exists()
    settlement = OrderSettlement.objects.get(order=order)
    assert settlement.status in (OrderSettlement.Status.PENDING, OrderSettlement.Status.SCHEDULED)
    assert len(paystack.calls) == 1
