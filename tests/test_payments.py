from datetime import timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, Order, OrderItem
from payments.models import OrderSettlement, Payment, WebhookEvent
from users.models import User


def _auth_client(user: User):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _build_order():
    owner = User.objects.create_user(email='owner-payments@example.com',
      phone='233555900001',
      password='StrongPass123!',
      role=User.Role.OWNER,
    )
    customer = User.objects.create_user(email='customer-payments@example.com',
      phone='233555900002',
      password='StrongPass123!',
    )
    service_type = Category.objects.create(name='Payment Wash', type=Category.CategoryType.SERVICE_TYPE)
    item_category = Category.objects.create(name='Payment Shirts', type=Category.CategoryType.ITEM_CATEGORY)
    item = LaunderableItem.objects.create(name='Payment Shirt', item_category=item_category)
    laundry = Laundry.objects.create(
      name='Payment Laundry',
      description='Payment test laundry',
      address='Accra',
      city='Accra',
      latitude='5.6037',
      longitude='-0.1870',
      phone_number='0240000002',
      owner=owner,
      status=Laundry.ApprovalStatus.APPROVED,
      is_active=True,
    )
    LaundryService.objects.create(
      laundry=laundry,
      item=item,
      service_type=service_type,
      price='25.00',
      is_available=True,
    )
    order = Order.objects.create(
      user=customer,
      laundry=laundry,
      pickup_date=timezone.now() + timedelta(days=1),
      total_amount='25.00',
      pickup_address='Pickup Address',
      delivery_address='Delivery Address',
    )
    OrderItem.objects.create(
      order=order,
      item=item,
      service_type=service_type,
      name='Payment Shirt',
      quantity=1,
      price='25.00',
    )
    return customer, order


def _build_pending_payment(reference='ORD-TEST-WEBHOOK'):
    customer, order = _build_order()
    payment = Payment.objects.create(
        user=customer,
        order=order,
        amount=Decimal('25.00'),
        currency='GHS',
        transaction_reference=reference,
        payment_method=Payment.Method.CARD,
        status=Payment.Status.PENDING,
    )
    return customer, order, payment


def _webhook_payload(payment, event_id='evt_webhook_success', **overrides):
    data = {
        'id': event_id,
        'status': 'success',
        'reference': payment.transaction_reference,
        'amount': 2500,
        'currency': 'GHS',
        'metadata': {
            'order_id': str(payment.order_id),
            'user_id': str(payment.user_id),
        },
    }
    data.update(overrides)
    return {
        'event': 'charge.success',
        'data': data,
    }


def _sign_webhook_body(body, secret='test-paystack-secret'):
    return hmac.new(secret.encode('utf-8'), body, hashlib.sha512).hexdigest()


def _post_signed_webhook(client, payload, secret='test-paystack-secret', signature=None):
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    return client.post(
        reverse('paystack_webhook'),
        data=body,
        content_type='application/json',
        HTTP_X_PAYSTACK_SIGNATURE=signature or _sign_webhook_body(body, secret),
    )


@pytest.mark.django_db
class TestPaymentFlow:
    @patch('payments.services.paystack.PaystackService.initialize_transaction')
    def test_payment_initialization_creates_record(self, mock_init):
        customer, order = _build_order()
        client = _auth_client(customer)
        mock_init.return_value = {
            'status': True,
            'data': {
                'authorization_url': 'https://paystack.example/authorize',
                'access_code': 'ACCESS123',
            }
        }

        with override_settings(ROOT_URLCONF='config.urls'):
            response = client.post(
                reverse('payment_initialize'),
                {'order_id': str(order.id), 'payment_method': 'CARD'},
                format='json',
            )

        assert response.status_code == status.HTTP_200_OK
        assert Payment.objects.filter(order=order, currency='GHS').exists()

    @patch('payments.services.paystack.PaystackService.initialize_transaction')
    def test_payment_reference_exists_before_provider_initialization(self, mock_init):
        customer, order = _build_order()

        def provider_call(*args, **kwargs):
            reference = kwargs.get('reference') or args[2]
            reserved = Payment.objects.get(order=order)
            assert reserved.transaction_reference == reference
            assert reserved.status == Payment.Status.PENDING
            assert reserved.paystack_reference is None
            return {
                'status': True,
                'data': {
                    'authorization_url': 'https://paystack.example/authorize',
                    'access_code': 'ACCESS-RESERVED',
                },
            }

        mock_init.side_effect = provider_call
        response = _auth_client(customer).post(
            reverse('payment_initialize'),
            {'order_id': str(order.id), 'payment_method': 'CARD'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        payment = Payment.objects.get(order=order)
        assert payment.paystack_reference == 'ACCESS-RESERVED'

    @patch('payments.services.paystack.PaystackService.initialize_transaction')
    def test_payment_timeout_retries_with_the_same_reserved_reference(self, mock_init):
        customer, order = _build_order()
        mock_init.side_effect = [
            {
                'status': False,
                'retryable': True,
                'indeterminate': True,
                'message': 'Payment provider did not respond in time. Please retry safely.',
            },
            {
                'status': True,
                'data': {
                    'authorization_url': 'https://paystack.example/authorize',
                    'access_code': 'ACCESS-RETRY',
                },
            },
        ]
        client = _auth_client(customer)
        payload = {'order_id': str(order.id), 'payment_method': 'CARD'}

        first = client.post(reverse('payment_initialize'), payload, format='json')
        reserved = Payment.objects.get(order=order)
        second = client.post(reverse('payment_initialize'), payload, format='json')

        assert first.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert second.status_code == status.HTTP_200_OK
        assert Payment.objects.filter(order=order).count() == 1
        assert mock_init.call_args_list[0].kwargs['reference'] == reserved.transaction_reference
        assert mock_init.call_args_list[1].kwargs['reference'] == reserved.transaction_reference
    @override_settings(PAYSTACK_APP_CALLBACK_URL='connect-laundry://orders/payment-callback')
    def test_https_callback_bridges_to_fixed_mobile_scheme(self):
        response = APIClient().get(
            reverse('payment_callback'),
            {'reference': 'ORD-CALLBACK-123'},
        )

        assert response.status_code == status.HTTP_302_FOUND
        assert response['Location'].startswith(
            'connect-laundry://orders/payment-callback?'
        )
        assert 'reference=ORD-CALLBACK-123' in response['Location']

    def test_https_callback_rejects_missing_reference(self):
        response = APIClient().get(reverse('payment_callback'))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
    @patch('payments.services.paystack.PaystackService.verify_transaction')
    def test_payment_verification_updates_payment_and_order(self, mock_verify):
        customer, order = _build_order()
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('25.00'),
            currency='GHS',
            transaction_reference='ORD-TEST-VERIFY',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.PENDING,
        )
        client = _auth_client(customer)
        mock_verify.return_value = {
            'status': True,
            'data': {
                'status': 'success',
                'amount': 2500,
                'currency': 'GHS',
                'reference': payment.transaction_reference,
                'metadata': {
                    'order_id': str(order.id),
                    'user_id': str(customer.id),
                },
            }
        }

        with override_settings(ROOT_URLCONF='config.urls'):
            response = client.get(reverse('payment_verify', kwargs={'reference': payment.transaction_reference}))

        assert response.status_code == status.HTTP_200_OK
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert order.status == Order.Status.CONFIRMED
        assert OrderSettlement.objects.filter(order=order).count() == 1
        assert order.payment_status == Order.PaymentStatus.PAID

    @patch('payments.services.paystack.PaystackService.verify_transaction')
    def test_payment_verification_rejects_missing_metadata(self, mock_verify):
        customer, order, payment = _build_pending_payment('ORD-VERIFY-NO-METADATA')
        mock_verify.return_value = {
            'status': True,
            'data': {
                'status': 'success',
                'reference': payment.transaction_reference,
                'amount': 2500,
                'currency': 'GHS',
                'metadata': {},
            },
        }

        response = _auth_client(customer).get(
            reverse('payment_verify', kwargs={'reference': payment.transaction_reference})
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert order.payment_status == Order.PaymentStatus.UNPAID
        assert not OrderSettlement.objects.filter(order=order).exists()

    @override_settings(PAYSTACK_SECRET_KEY='sk_live_example')
    @patch('payments.services.paystack.PaystackService.verify_transaction')
    def test_live_backend_rejects_test_transaction_domain(self, mock_verify):
        customer, order, payment = _build_pending_payment('ORD-VERIFY-WRONG-DOMAIN')
        mock_verify.return_value = {
            'status': True,
            'data': {
                'domain': 'test',
                'status': 'success',
                'reference': payment.transaction_reference,
                'amount': 2500,
                'currency': 'GHS',
                'metadata': {
                    'order_id': str(order.id),
                    'user_id': str(customer.id),
                },
            },
        }

        response = _auth_client(customer).get(
            reverse('payment_verify', kwargs={'reference': payment.transaction_reference})
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
    @patch('payments.services.paystack.PaystackService.verify_transaction')
    def test_payment_verification_rejects_amount_mismatch(self, mock_verify):
        customer, order = _build_order()
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('25.00'),
            currency='GHS',
            transaction_reference='ORD-TEST-MISMATCH',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.PENDING,
        )
        client = _auth_client(customer)
        mock_verify.return_value = {
            'status': True,
            'data': {
                'status': 'success',
                'amount': 2400,
                'currency': 'GHS',
                'reference': payment.transaction_reference,
                'metadata': {
                    'order_id': str(order.id),
                    'user_id': str(customer.id),
                },
            }
        }

        with override_settings(ROOT_URLCONF='config.urls'):
            response = client.get(reverse('payment_verify', kwargs={'reference': payment.transaction_reference}))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert order.payment_status == Order.PaymentStatus.UNPAID


@pytest.mark.django_db
class TestPaystackWebhookAbuse:
    @pytest.fixture(autouse=True)
    def webhook_settings(self, settings):
        settings.ROOT_URLCONF = 'config.urls'
        settings.PAYSTACK_SECRET_KEY = 'test-paystack-secret'
        settings.PAYMENT_CURRENCY = 'GHS'

    def test_webhook_rejects_invalid_signature_without_state_change(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-BAD-SIG')
        client = APIClient()

        response = _post_signed_webhook(
            client,
            _webhook_payload(payment, event_id='evt_bad_sig'),
            signature='not-a-valid-signature',
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.PENDING
        assert order.payment_status == Order.PaymentStatus.UNPAID
        assert WebhookEvent.objects.count() == 0

    def test_webhook_rejects_malformed_signed_payload(self):
        client = APIClient()
        body = b'{"event":'

        response = client.post(
            reverse('paystack_webhook'),
            data=body,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=_sign_webhook_body(body),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert WebhookEvent.objects.count() == 0

    def test_webhook_confirms_payment_once_and_ignores_duplicate_event(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-DUPE')
        client = APIClient()
        payload = _webhook_payload(payment, event_id='evt_duplicate_success')

        first = _post_signed_webhook(client, payload)
        second = _post_signed_webhook(client, payload)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert WebhookEvent.objects.filter(event_id='evt_duplicate_success').count() == 1
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert order.payment_status == Order.PaymentStatus.PAID
        assert order.status == Order.Status.CONFIRMED
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_webhook_rejects_duplicate_replay_after_success_without_double_mutation(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-REPLAY')
        client = APIClient()

        success = _post_signed_webhook(
            client,
            _webhook_payload(payment, event_id='evt_replay_original'),
        )
        replay = _post_signed_webhook(
            client,
            _webhook_payload(payment, event_id='evt_replay_original', amount=999999),
        )

        assert success.status_code == status.HTTP_200_OK
        assert replay.status_code == status.HTTP_200_OK
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert payment.raw_response['amount'] == 2500
        assert order.payment_status == Order.PaymentStatus.PAID

    def test_webhook_replay_protected_when_event_id_missing(self):
        # When Paystack omits data.id, replay protection must fall back to a
        # content hash so a replayed payload is still ignored.
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-NOID')
        client = APIClient()
        payload = _webhook_payload(payment, event_id=None)

        first = _post_signed_webhook(client, payload)
        second = _post_signed_webhook(client, payload)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        # Exactly one dedup record created, keyed by the payload hash.
        assert WebhookEvent.objects.count() == 1
        assert WebhookEvent.objects.first().event_id.startswith('sha512:')
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    def test_webhook_rejects_amount_mismatch(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-AMOUNT')
        client = APIClient()

        response = _post_signed_webhook(
            client,
            _webhook_payload(payment, event_id='evt_amount_mismatch', amount=2400),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert payment.raw_response['amount'] == 2400
        assert order.payment_status == Order.PaymentStatus.UNPAID

    def test_webhook_rejects_currency_mismatch(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-CURRENCY')
        client = APIClient()

        response = _post_signed_webhook(
            client,
            _webhook_payload(payment, event_id='evt_currency_mismatch', currency='NGN'),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert payment.raw_response['currency'] == 'NGN'
        assert order.payment_status == Order.PaymentStatus.UNPAID

    def test_webhook_rejects_order_metadata_mismatch(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-ORDER')
        client = APIClient()
        metadata = {
            'order_id': str(uuid.uuid4()),
            'user_id': str(payment.user_id),
        }

        response = _post_signed_webhook(
            client,
            _webhook_payload(payment, event_id='evt_order_mismatch', metadata=metadata),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert order.payment_status == Order.PaymentStatus.UNPAID

    def test_webhook_rejects_user_metadata_mismatch(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-USER')
        client = APIClient()
        metadata = {
            'order_id': str(payment.order_id),
            'user_id': str(uuid.uuid4()),
        }

        response = _post_signed_webhook(
            client,
            _webhook_payload(payment, event_id='evt_user_mismatch', metadata=metadata),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert order.payment_status == Order.PaymentStatus.UNPAID

    def test_get_payment_status_success(self):
        customer, order, payment = _build_pending_payment('ORD-STATUS-SUCCESS')
        client = _auth_client(customer)
        response = client.get(reverse('payment_status', kwargs={'reference': payment.transaction_reference}))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['status'] == 'success'
        assert data['data']['payment_status'] == Payment.Status.PENDING
        assert data['data']['order_status'] == order.status

    def test_get_payment_status_not_owner_returns_404(self):
        _, _, payment = _build_pending_payment('ORD-STATUS-NOT-OWNER')
        other_user = User.objects.create_user(
            email='other-payments-status@example.com',
            phone='233555900999',
            password='StrongPass123!',
        )
        client = _auth_client(other_user)
        response = client.get(reverse('payment_status', kwargs={'reference': payment.transaction_reference}))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestHardenPaymentAudit:
    @patch('payments.services.paystack.PaystackService.initialize_transaction')
    def test_payment_initialize_idempotency(self, mock_init):
        customer, order = _build_order()
        client = _auth_client(customer)
        mock_init.return_value = {
            'status': True,
            'data': {
                'authorization_url': 'https://paystack.example/authorize',
                'access_code': 'ACCESS123',
            }
        }

        # Initialize first time
        response_1 = client.post(
            reverse('payment_initialize'),
            {'order_id': str(order.id), 'payment_method': 'CARD'},
            format='json',
        )
        assert response_1.status_code == status.HTTP_200_OK
        ref_1 = response_1.json()['data']['reference']

        # Initialize second time with same parameters
        response_2 = client.post(
            reverse('payment_initialize'),
            {'order_id': str(order.id), 'payment_method': 'CARD'},
            format='json',
        )
        assert response_2.status_code == status.HTTP_200_OK
        ref_2 = response_2.json()['data']['reference']

        # Assert same reference is returned
        assert ref_1 == ref_2
        assert Payment.objects.filter(order=order).count() == 1

    @patch('payments.services.paystack.PaystackService.initialize_transaction')
    def test_old_pending_payment_is_reused_without_new_provider_charge(self, mock_init):
        customer, order, payment = _build_pending_payment('ORD-PENDING-REUSE')
        payment.paystack_reference = 'OLD-ACCESS-CODE'
        payment.save(update_fields=['paystack_reference'])
        Payment.objects.filter(id=payment.id).update(
            created_at=timezone.now() - timezone.timedelta(hours=3)
        )

        response = _auth_client(customer).post(
            reverse('payment_initialize'),
            {'order_id': str(order.id), 'payment_method': 'CARD'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()['data']['reference'] == payment.transaction_reference
        assert response.json()['data']['authorization_url'].endswith('OLD-ACCESS-CODE')
        mock_init.assert_not_called()
    def test_payment_state_machine_validation(self):
        customer, order = _build_order()
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('25.00'),
            currency='GHS',
            transaction_reference='ORD-STATE-TEST',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.SUCCESS,
        )

        # A settled payment can only move on to a refund, never back to FAILED.
        with pytest.raises(ValueError, match="Cannot transition payment from 'SUCCESS' to 'FAILED'"):
            payment.transition_to(Payment.Status.FAILED)

    @patch('payments.services.paystack.PaystackService.verify_transaction')
    def test_celery_reconciliation_task(self, mock_verify):
        from payments.tasks import reconcile_pending_payments
        from django.utils import timezone
        
        customer, order, payment = _build_pending_payment('ORD-RECONCILE-TEST')
        # Set created_at to 15 minutes ago to trigger reconciliation
        Payment.objects.filter(id=payment.id).update(created_at=timezone.now() - timezone.timedelta(minutes=15))
        
        mock_verify.return_value = {
            'status': True,
            'data': {
                'status': 'success',
                'amount': 2500,
                'currency': 'GHS',
                'reference': payment.transaction_reference,
                'metadata': {
                    'order_id': str(order.id),
                    'user_id': str(customer.id),
                },
            }
        }

        # Run task
        result = reconcile_pending_payments()
        assert "Reconciled 1 payments" in result

        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert order.payment_status == Order.PaymentStatus.PAID
        assert order.status == Order.Status.CONFIRMED
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_get_receipt_unauthorized_returns_404(self):
        customer, order, payment = _build_pending_payment('ORD-RECEIPT-AUTH')
        other_user = User.objects.create_user(
            email='other-receipt-user@example.com',
            phone='233555900888',
            password='StrongPass123!',
        )
        client = _auth_client(other_user)
        response = client.get(reverse('payment_receipt', kwargs={'reference': payment.transaction_reference}))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_receipt_owner_success(self):
        customer, order, payment = _build_pending_payment('ORD-RECEIPT-SUCCESS')
        payment.status = Payment.Status.SUCCESS
        payment.paid_at = timezone.now()
        payment.save()

        client = _auth_client(customer)
        response = client.get(reverse('payment_receipt', kwargs={'reference': payment.transaction_reference}))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['transaction_reference'] == payment.transaction_reference
        assert data['order_no'] == order.order_no

    def test_get_analytics_and_owner_stats(self):
        customer, order, payment = _build_pending_payment('ORD-ANALYTICS-TEST')
        payment.status = Payment.Status.SUCCESS
        payment.paid_at = timezone.now()
        payment.save()

        # Try with customer (must fail)
        client_cust = _auth_client(customer)
        response_anal = client_cust.get(reverse('payment_analytics'))
        assert response_anal.status_code == status.HTTP_403_FORBIDDEN

        # Create admin and test analytics
        admin_user = User.objects.create_user(
            email='admin-analytics@example.com',
            phone='233555900777',
            password='StrongPass123!',
            role='ADMIN'
        )
        client_admin = _auth_client(admin_user)
        response_anal_admin = client_admin.get(reverse('payment_analytics'))
        assert response_anal_admin.status_code == status.HTTP_200_OK
        data_anal = response_anal_admin.json()
        assert float(data_anal['total_revenue']) == 25.00

        # Create owner and test owner-stats
        owner_user = User.objects.create_user(
            email='owner-stats-user@example.com',
            phone='233555900666',
            password='StrongPass123!',
            role='OWNER'
        )
        # Reassign laundry owner to verify stats filtering
        order.laundry.owner = owner_user
        order.laundry.save()

        client_owner = _auth_client(owner_user)
        response_owner_stats = client_owner.get(reverse('payment_owner_stats'))
        assert response_owner_stats.status_code == status.HTTP_200_OK
        data_owner = response_owner_stats.json()
        assert float(data_owner['total_revenue']) == 25.00



@pytest.mark.django_db
class TestPaystackWebhookRetrySafety:
    """A failed delivery must stay retryable.

    Regression: the replay-protection row used to be committed before the
    processing transaction opened. Any transient failure burned the dedup key,
    so Paystack's retry short-circuited to 200 and the payment was left
    PENDING forever — money taken, order never confirmed.
    """

    @pytest.fixture(autouse=True)
    def webhook_settings(self, settings):
        settings.ROOT_URLCONF = 'config.urls'
        settings.PAYSTACK_SECRET_KEY = 'test-paystack-secret'
        settings.PAYMENT_CURRENCY = 'GHS'

    def test_transient_failure_leaves_event_replayable(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-RETRY')
        client = APIClient()
        payload = _webhook_payload(payment, event_id='evt_transient_failure')

        # First delivery blows up midway through processing.
        with patch(
            'payments.webhooks.OrderStateMachine.transition',
            side_effect=RuntimeError('transient database blip'),
        ):
            first = _post_signed_webhook(client, payload)

        assert first.status_code == 500
        payment.refresh_from_db()
        assert payment.status == Payment.Status.PENDING
        # The claim must have rolled back with the failed work.
        assert not WebhookEvent.objects.filter(event_id='evt_transient_failure').exists()

        # Paystack retries the same event — it must now be processed.
        second = _post_signed_webhook(client, payload)

        assert second.status_code == 200
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert order.payment_status == Order.PaymentStatus.PAID
        assert WebhookEvent.objects.filter(event_id='evt_transient_failure').count() == 1

    def test_successful_event_is_still_claimed_once(self):
        _, order, payment = _build_pending_payment('ORD-WEBHOOK-CLAIM-ONCE')
        client = APIClient()
        payload = _webhook_payload(payment, event_id='evt_claim_once')

        first = _post_signed_webhook(client, payload)
        second = _post_signed_webhook(client, payload)

        assert first.status_code == 200
        assert second.status_code == 200
        assert WebhookEvent.objects.filter(event_id='evt_claim_once').count() == 1
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS

    def test_signature_comparison_is_constant_time(self):
        """compare_digest rejects a wrong digest of identical length."""
        _, _, payment = _build_pending_payment('ORD-WEBHOOK-CT')
        client = APIClient()
        payload = _webhook_payload(payment, event_id='evt_ct')
        # Same length as a real sha512 hexdigest, so only the bytes differ.
        forged = '0' * 128

        response = _post_signed_webhook(client, payload, signature=forged)

        assert response.status_code == 401
        payment.refresh_from_db()
        assert payment.status == Payment.Status.PENDING
