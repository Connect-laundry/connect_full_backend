"""
Sending money out of the platform account.

Every test here is asking one of two questions: could this pay the wrong
laundry, or could it pay the right one twice? The guards are the feature, so
they are what is tested.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from ordering.services.finance_service import FinanceService
from payments.models import OrderSettlement, Payout
from payments.services.payout_service import (
    PayoutError,
    PayoutService,
    transfer_reference,
    transfers_enabled,
)
from payments.services.settlement_service import SettlementService

from test_payments import _build_order


def _payout_ready(recipient='RCP_test123'):
    _, order = _build_order()
    with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
        FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    SettlementService.record_for_order(order)
    SettlementService.release_for_order(order, confirmed=True)

    order.laundry.paystack_recipient_code = recipient
    order.laundry.save(update_fields=['paystack_recipient_code'])

    payout = SettlementService.build_payout(order.laundry)
    payout.refresh_from_db()
    return order, payout


def _accepting_client():
    client = MagicMock()
    client.initiate_transfer.return_value = {'status': True, 'data': {'status': 'pending'}}
    return client


@pytest.mark.django_db
class TestGuards:
    def test_transfers_are_off_by_default(self):
        assert transfers_enabled() is False

    def test_nothing_is_sent_while_transfers_are_off(self):
        _, payout = _payout_ready()
        client = _accepting_client()

        with pytest.raises(PayoutError, match='switched off'):
            PayoutService.send(payout, paystack=client)

        client.initiate_transfer.assert_not_called()

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_a_laundry_without_a_recipient_is_not_paid(self):
        _, payout = _payout_ready(recipient='')
        client = _accepting_client()

        with pytest.raises(PayoutError, match='recipient code'):
            PayoutService.send(payout, paystack=client)

        client.initiate_transfer.assert_not_called()

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True, PAYOUT_MAX_TRANSFER_AMOUNT='10.00')
    def test_an_oversized_payout_is_refused(self):
        # A pricing bug should stop here, not at somebody's bank.
        _, payout = _payout_ready()
        client = _accepting_client()

        with pytest.raises(PayoutError, match='ceiling'):
            PayoutService.send(payout, paystack=client)

        client.initiate_transfer.assert_not_called()

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_an_edited_amount_is_refused(self):
        # The payout row and its settlements must agree. If they do not,
        # guessing which one is right is not this code's job.
        _, payout = _payout_ready()
        payout.amount = payout.amount + Decimal('100.00')
        payout.save(update_fields=['amount'])
        client = _accepting_client()

        with pytest.raises(PayoutError, match='does not match'):
            PayoutService.send(payout, paystack=client)

        client.initiate_transfer.assert_not_called()

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_an_already_sent_payout_is_not_sent_again(self):
        _, payout = _payout_ready()
        payout.status = Payout.Status.PAID
        payout.save(update_fields=['status'])
        client = _accepting_client()

        with pytest.raises(PayoutError, match='not DRAFT'):
            PayoutService.send(payout, paystack=client)

        client.initiate_transfer.assert_not_called()


@pytest.mark.django_db
class TestReference:
    def test_the_reference_is_stable_for_a_payout(self):
        # Paystack rejects a duplicate reference, and that is what stops a
        # retry from paying a laundry twice.
        _, payout = _payout_ready()
        assert transfer_reference(payout) == transfer_reference(payout)

    def test_different_payouts_get_different_references(self):
        _, first = _payout_ready()
        second = Payout.objects.create(
            laundry=first.laundry, amount=Decimal('5.00'), currency='GHS',
        )
        assert transfer_reference(first) != transfer_reference(second)


@pytest.mark.django_db
class TestSending:
    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_an_accepted_transfer_leaves_the_payout_processing(self):
        # Paystack accepting a request is not the money landing. Marking it
        # paid here would be a lie the ledger cannot take back.
        _, payout = _payout_ready()
        client = _accepting_client()

        result = PayoutService.send(payout, paystack=client)

        assert result.status == Payout.Status.PROCESSING
        assert result.paid_at is None
        assert result.reference == transfer_reference(payout)
        client.initiate_transfer.assert_called_once()

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_a_rejected_transfer_returns_the_payout_to_draft(self):
        # Nothing was sent, so it can be corrected and retried.
        _, payout = _payout_ready()
        client = MagicMock()
        client.initiate_transfer.return_value = {'status': False, 'message': 'Invalid recipient'}

        with pytest.raises(PayoutError, match='Invalid recipient'):
            PayoutService.send(payout, paystack=client)

        payout.refresh_from_db()
        assert payout.status == Payout.Status.DRAFT
        assert 'Invalid recipient' in payout.failure_reason

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_an_unknown_outcome_is_left_alone_for_a_human(self):
        # A timeout may mean the money left. Retrying could pay twice, so this
        # parks in PROCESSING rather than choosing either risk.
        _, payout = _payout_ready()
        client = MagicMock()
        client.initiate_transfer.return_value = {
            'status': False, 'indeterminate': True, 'message': 'Transfer request timed out.'
        }

        with pytest.raises(PayoutError, match='unknown'):
            PayoutService.send(payout, paystack=client)

        payout.refresh_from_db()
        assert payout.status == Payout.Status.PROCESSING

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_the_amount_sent_is_the_payout_amount(self):
        _, payout = _payout_ready()
        client = _accepting_client()

        PayoutService.send(payout, paystack=client)

        kwargs = client.initiate_transfer.call_args.kwargs
        assert Decimal(str(kwargs['amount'])) == payout.amount
        assert kwargs['recipient_code'] == 'RCP_test123'


@pytest.mark.django_db
class TestTransferOutcomes:
    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_success_settles_the_payout_and_its_debts(self):
        order, payout = _payout_ready()
        PayoutService.send(payout, paystack=_accepting_client())

        PayoutService.mark_transfer_settled(payout, reference='TRF-OK')

        payout.refresh_from_db()
        assert payout.status == Payout.Status.PAID
        assert payout.paid_at is not None
        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.PAID

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_failure_returns_the_money_to_the_payable_pool(self):
        # The laundry is still owed it. Losing that from the ledger because a
        # bank rejected an account number would be worse than the failure.
        order, payout = _payout_ready()
        PayoutService.send(payout, paystack=_accepting_client())

        PayoutService.mark_transfer_failed(payout, reason='Account closed')

        payout.refresh_from_db()
        settlement = OrderSettlement.objects.get(order=order)
        assert payout.status == Payout.Status.FAILED
        assert 'Account closed' in payout.failure_reason
        assert settlement.status == OrderSettlement.Status.PENDING
        assert settlement.payout_id is None
        assert SettlementService.outstanding_total(order.laundry) == settlement.net_payable

    @override_settings(PAYSTACK_TRANSFERS_ENABLED=True)
    def test_a_failed_payout_can_be_rebuilt_and_sent_again(self):
        order, payout = _payout_ready()
        PayoutService.send(payout, paystack=_accepting_client())
        PayoutService.mark_transfer_failed(payout, reason='Account closed')

        retry = SettlementService.build_payout(order.laundry)

        assert retry is not None
        assert retry.id != payout.id
        assert retry.amount == order.total_amount
