"""Refund lifecycle: request -> REFUND_PENDING -> settled by webhook -> REFUNDED."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from ordering.models import Order
from payments.models import Payment
from users.models import User

from .test_payments import (
    _auth_client,
    _build_pending_payment,
    _post_signed_webhook,
)


@pytest.mark.django_db
class TestRefundFlow:
    @pytest.fixture(autouse=True)
    def refund_settings(self, settings):
        settings.ROOT_URLCONF = 'config.urls'
        settings.PAYSTACK_SECRET_KEY = 'test-paystack-secret'
        settings.PAYMENT_CURRENCY = 'GHS'

    def _paid_payment(self, reference):
        customer, order, payment = _build_pending_payment(reference)
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=['status'])
        order.payment_status = Order.PaymentStatus.PAID
        order.save(update_fields=['payment_status'])
        return customer, order, payment

    def _staff(self):
        return User.objects.create_user(
            email='refund-staff@example.com',
            phone='233555910001',
            password='StrongPass123!',
            is_staff=True,
            role=User.Role.OWNER,
        )

    # ---- permissions -----------------------------------------------------

    def test_customer_cannot_refund_their_own_payment(self):
        customer, _, payment = self._paid_payment('ORD-REFUND-PERM')
        client = _auth_client(customer)

        response = client.post(
            reverse('payment_refund', args=[payment.transaction_reference]), {}, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    def test_anonymous_cannot_refund(self):
        _, _, payment = self._paid_payment('ORD-REFUND-ANON')

        response = APIClient().post(
            reverse('payment_refund', args=[payment.transaction_reference]), {}, format='json')

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # ---- request ---------------------------------------------------------

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_staff_refund_moves_payment_to_refund_pending(self, mock_refund):
        mock_refund.return_value = {'status': True, 'data': {'id': 1}}
        _, _, payment = self._paid_payment('ORD-REFUND-OK')
        client = _auth_client(self._staff())

        response = client.post(
            reverse('payment_refund', args=[payment.transaction_reference]),
            {'reason': 'Customer cancelled'}, format='json')

        assert response.status_code == status.HTTP_200_OK, response.data
        payment.refresh_from_db()
        # Not REFUNDED yet — Paystack settles asynchronously.
        assert payment.status == Payment.Status.REFUND_PENDING

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_gateway_rejection_leaves_payment_untouched(self, mock_refund):
        mock_refund.return_value = {'status': False, 'message': 'Transaction not refundable'}
        _, _, payment = self._paid_payment('ORD-REFUND-REJECT')
        client = _auth_client(self._staff())

        response = client.post(
            reverse('payment_refund', args=[payment.transaction_reference]), {}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_unpaid_payment_cannot_be_refunded(self, mock_refund):
        _, _, payment = _build_pending_payment('ORD-REFUND-UNPAID')
        client = _auth_client(self._staff())

        response = client.post(
            reverse('payment_refund', args=[payment.transaction_reference]), {}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_refund.assert_not_called()

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_double_refund_is_rejected(self, mock_refund):
        mock_refund.return_value = {'status': True, 'data': {'id': 1}}
        _, _, payment = self._paid_payment('ORD-REFUND-DOUBLE')
        client = _auth_client(self._staff())
        url = reverse('payment_refund', args=[payment.transaction_reference])

        first = client.post(url, {}, format='json')
        second = client.post(url, {}, format='json')

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert mock_refund.call_count == 1

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_partial_refund_cannot_exceed_amount_paid(self, mock_refund):
        _, _, payment = self._paid_payment('ORD-REFUND-OVER')
        client = _auth_client(self._staff())

        response = client.post(
            reverse('payment_refund', args=[payment.transaction_reference]),
            {'amount': str(payment.amount + Decimal('10.00'))}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_refund.assert_not_called()

    # ---- settlement via webhook -----------------------------------------

    def _refund_event(self, payment, event_type, event_id):
        return {
            'event': event_type,
            'data': {
                'id': event_id,
                'status': 'processed',
                'transaction': {'reference': payment.transaction_reference},
            },
        }

    def test_refund_processed_webhook_settles_the_refund(self):
        _, order, payment = self._paid_payment('ORD-REFUND-SETTLE')
        payment.status = Payment.Status.REFUND_PENDING
        payment.save(update_fields=['status'])

        response = _post_signed_webhook(
            APIClient(), self._refund_event(payment, 'refund.processed', 'evt_refund_ok'))

        assert response.status_code == 200
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.REFUNDED
        assert order.payment_status == Order.PaymentStatus.REFUNDED

    def test_repeat_refund_webhook_is_idempotent(self):
        _, _, payment = self._paid_payment('ORD-REFUND-IDEM')
        payment.status = Payment.Status.REFUND_PENDING
        payment.save(update_fields=['status'])
        client = APIClient()
        event = self._refund_event(payment, 'refund.processed', 'evt_refund_idem')

        first = _post_signed_webhook(client, event)
        second = _post_signed_webhook(client, event)

        assert first.status_code == 200
        assert second.status_code == 200
        payment.refresh_from_db()
        assert payment.status == Payment.Status.REFUNDED

    def test_refund_failed_webhook_restores_the_payment(self):
        _, _, payment = self._paid_payment('ORD-REFUND-FAIL')
        payment.status = Payment.Status.REFUND_PENDING
        payment.save(update_fields=['status'])

        response = _post_signed_webhook(
            APIClient(), self._refund_event(payment, 'refund.failed', 'evt_refund_fail'))

        assert response.status_code == 200
        payment.refresh_from_db()
        # Back to SUCCESS so an admin can retry the refund.
        assert payment.status == Payment.Status.SUCCESS

    def test_refund_webhook_for_unknown_reference_is_a_safe_no_op(self):
        payload = {
            'event': 'refund.processed',
            'data': {
                'id': 'evt_refund_unknown',
                'transaction': {'reference': 'ORD-DOES-NOT-EXIST'},
            },
        }

        response = _post_signed_webhook(APIClient(), payload)

        assert response.status_code == 200
