import pytest
from unittest.mock import patch
from django.urls import reverse
from rest_framework import status

from payments.models import Payment
from test_payments import _auth_client, _build_pending_payment


@pytest.mark.django_db
class TestPaymentAwareOrderLifecycle:
    def test_laundry_cannot_accept_pending_paystack_order(self):
        _, order, _ = _build_pending_payment('ORD-LIFECYCLE-ACCEPT')

        response = _auth_client(order.laundry.owner).patch(
            reverse('order-lifecycle-accept', kwargs={'pk': order.id}),
            {},
            format='json',
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        order.refresh_from_db()
        assert order.status == order.Status.PENDING

    @patch('payments.services.paystack.PaystackService.verify_transaction')
    def test_customer_can_cancel_uncompleted_pending_paystack_order(self, mock_verify):
        mock_verify.return_value = {'status': False, 'message': 'Transaction not found'}
        customer, order, payment = _build_pending_payment('ORD-LIFECYCLE-CANCEL')

        response = _auth_client(customer).patch(
            reverse('order-lifecycle-cancel', kwargs={'pk': order.id}),
            {'reason': 'Changed my mind'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == order.Status.CANCELLED
        assert payment.status == Payment.Status.FAILED

    def test_customer_can_cancel_pending_order_without_reference(self):
        customer, order, payment = _build_pending_payment('ORD-LIFECYCLE-CANCEL-NOREF')
        payment.transaction_reference = None
        payment.save(update_fields=['transaction_reference'])

        response = _auth_client(customer).patch(
            reverse('order-lifecycle-cancel', kwargs={'pk': order.id}),
            {'reason': 'Changed my mind'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == order.Status.CANCELLED
        assert payment.status == Payment.Status.FAILED

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_customer_can_cancel_successful_payment_order_with_refund(self, mock_refund):
        mock_refund.return_value = {'status': True, 'data': {'id': 1}}
        customer, order, payment = _build_pending_payment('ORD-LIFECYCLE-CANCEL-PAID')
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=['status'])
        order.status = order.Status.CONFIRMED
        order.payment_status = order.PaymentStatus.PAID
        order.save(update_fields=['status', 'payment_status'])

        response = _auth_client(customer).patch(
            reverse('order-lifecycle-cancel', kwargs={'pk': order.id}),
            {'reason': 'Changed my mind'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == order.Status.CANCELLED
        assert payment.status == Payment.Status.REFUND_PENDING

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_laundry_can_reject_successful_payment_order_with_refund(self, mock_refund):
        mock_refund.return_value = {'status': True, 'data': {'id': 1}}
        _, order, payment = _build_pending_payment('ORD-LIFECYCLE-REJECT')
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=['status'])

        response = _auth_client(order.laundry.owner).patch(
            reverse('order-lifecycle-reject', kwargs={'pk': order.id}),
            {'reason': 'Cannot fulfill'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == order.Status.REJECTED
        assert payment.status == Payment.Status.REFUND_PENDING

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_rejection_blocked_if_gateway_refund_fails(self, mock_refund):
        mock_refund.return_value = {'status': False, 'message': 'Insufficient balance for refund'}
        _, order, payment = _build_pending_payment('ORD-LIFECYCLE-REJECT-FAIL')
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=['status'])

        response = _auth_client(order.laundry.owner).patch(
            reverse('order-lifecycle-reject', kwargs={'pk': order.id}),
            {'reason': 'Cannot fulfill'},
            format='json',
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        order.refresh_from_db()
        assert order.status == order.Status.PENDING

    def test_cash_order_can_still_be_accepted_while_payment_is_pending(self):
        _, order, payment = _build_pending_payment('ORD-LIFECYCLE-CASH')
        payment.payment_method = Payment.Method.CASH
        payment.save(update_fields=['payment_method'])
        order.payment_method = order.PaymentMethod.CASH
        order.save(update_fields=['payment_method', 'updated_at'])

        response = _auth_client(order.laundry.owner).patch(
            reverse('order-lifecycle-accept', kwargs={'pk': order.id}),
            {},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == order.Status.CONFIRMED
@pytest.mark.django_db
class TestOrderMutationBoundary:
    @pytest.mark.parametrize('method', ['patch', 'put', 'delete'])
    def test_generic_order_mutations_are_not_exposed(self, method):
        customer, order, _ = _build_pending_payment(f'ORD-NO-{method.upper()}')
        client = _auth_client(customer)
        request = getattr(client, method)
        response = request(
            reverse('order-detail', kwargs={'pk': order.id}),
            {'pickup_address': 'Tampered address'} if method != 'delete' else None,
            format='json',
        )

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert order.__class__.objects.filter(pk=order.pk).exists()