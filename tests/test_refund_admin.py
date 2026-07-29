"""Django admin refund action.

Refunds move money irreversibly, so this is a per-payment confirmation screen
rather than a bulk action, and it must reject anyone who is not staff.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from ordering.models import Order
from payments.models import Payment
from users.models import User

from .test_payments import _build_pending_payment


@pytest.mark.django_db
class TestRefundAdminAction:
    @pytest.fixture(autouse=True)
    def admin_settings(self, settings):
        settings.ROOT_URLCONF = 'config.urls'
        settings.PAYSTACK_SECRET_KEY = 'test-paystack-secret'

    def _paid_payment(self, reference):
        customer, order, payment = _build_pending_payment(reference)
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=['status'])
        order.payment_status = Order.PaymentStatus.PAID
        order.save(update_fields=['payment_status'])
        return customer, order, payment

    def _staff_client(self):
        staff = User.objects.create_user(
            email='refund-admin@example.com',
            phone='233555920001',
            password='StrongPass123!',
            is_staff=True,
            is_superuser=True,
            role=User.Role.OWNER,
        )
        client = Client()
        client.force_login(staff)
        return client

    def _url(self, payment):
        return reverse('admin:payments_payment_refund', args=[payment.pk])

    # ---- access ----------------------------------------------------------

    def test_non_staff_cannot_open_the_refund_screen(self):
        customer, _, payment = self._paid_payment('ORD-ADMIN-REFUND-PERM')
        client = Client()
        client.force_login(customer)

        response = client.get(self._url(payment))

        # Django admin bounces non-staff to the login screen.
        assert response.status_code == 302
        assert '/admin/login/' in response['Location']
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_non_staff_cannot_post_a_refund(self, mock_refund):
        customer, _, payment = self._paid_payment('ORD-ADMIN-REFUND-POST')
        client = Client()
        client.force_login(customer)

        client.post(self._url(payment), {'reason': 'nope'})

        mock_refund.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    # ---- confirmation screen --------------------------------------------

    def test_get_renders_a_confirmation_without_refunding(self):
        _, _, payment = self._paid_payment('ORD-ADMIN-REFUND-GET')
        client = self._staff_client()

        with patch('payments.services.refund.PaystackService.refund_transaction') as mock_refund:
            response = client.get(self._url(payment))

        assert response.status_code == 200
        assert b'Refund this payment' in response.content
        # A GET must never move money.
        mock_refund.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    def test_unrefundable_payment_is_rejected_before_the_form(self):
        _, _, payment = _build_pending_payment('ORD-ADMIN-REFUND-PENDING')
        client = self._staff_client()

        response = client.get(self._url(payment))

        assert response.status_code == 302
        payment.refresh_from_db()
        assert payment.status == Payment.Status.PENDING

    # ---- performing the refund ------------------------------------------

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_full_refund_moves_payment_to_refund_pending(self, mock_refund):
        mock_refund.return_value = {'status': True, 'data': {'id': 1}}
        _, _, payment = self._paid_payment('ORD-ADMIN-REFUND-FULL')
        client = self._staff_client()

        response = client.post(self._url(payment), {'amount': '', 'reason': 'Order cancelled'})

        assert response.status_code == 302
        payment.refresh_from_db()
        assert payment.status == Payment.Status.REFUND_PENDING
        # A blank amount means a full refund.
        assert mock_refund.call_args.kwargs['amount'] is None

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_partial_refund_passes_the_amount_through(self, mock_refund):
        mock_refund.return_value = {'status': True, 'data': {'id': 1}}
        _, _, payment = self._paid_payment('ORD-ADMIN-REFUND-PARTIAL')
        client = self._staff_client()

        client.post(self._url(payment), {'amount': '5.00', 'reason': 'Partial'})

        assert mock_refund.call_args.kwargs['amount'] == Decimal('5.00')
        payment.refresh_from_db()
        assert payment.status == Payment.Status.REFUND_PENDING

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_non_numeric_amount_is_rejected(self, mock_refund):
        _, _, payment = self._paid_payment('ORD-ADMIN-REFUND-BADNUM')
        client = self._staff_client()

        client.post(self._url(payment), {'amount': 'abc'})

        mock_refund.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_over_refund_is_rejected(self, mock_refund):
        _, _, payment = self._paid_payment('ORD-ADMIN-REFUND-OVER')
        client = self._staff_client()

        client.post(self._url(payment), {'amount': str(payment.amount + Decimal('1.00'))})

        mock_refund.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_gateway_rejection_leaves_payment_untouched(self, mock_refund):
        mock_refund.return_value = {'status': False, 'message': 'Not refundable'}
        _, _, payment = self._paid_payment('ORD-ADMIN-REFUND-GWFAIL')
        client = self._staff_client()

        client.post(self._url(payment), {})

        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    @patch('payments.services.refund.PaystackService.refund_transaction')
    def test_refund_is_not_offered_twice(self, mock_refund):
        mock_refund.return_value = {'status': True, 'data': {'id': 1}}
        _, _, payment = self._paid_payment('ORD-ADMIN-REFUND-TWICE')
        client = self._staff_client()

        client.post(self._url(payment), {})
        client.post(self._url(payment), {})

        assert mock_refund.call_count == 1
        payment.refresh_from_db()
        assert payment.status == Payment.Status.REFUND_PENDING
