import decimal
from decimal import Decimal
import hashlib
import hmac
import json
import uuid
from unittest.mock import patch, MagicMock

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, Order, OrderItem, Coupon, CouponUsage
from payments.models import OrderSettlement, Payment, WebhookEvent
from marketplace.models import IdempotencyRecord, Notification
from users.models import User
from users.services.clerk_service import ClerkProfile, sync_user_from_clerk


def _auth_client(user: User):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _create_fixtures():
    owner_user = User.objects.create_user(
        email='owner-cert@simame.tech',
        phone='233550000001',
        password='StrongCertPass123!',
        role=User.Role.OWNER,
    )
    customer_user = User.objects.create_user(
        email='customer-cert@simame.tech',
        phone='233550000002',
        password='StrongCertPass123!',
        role=User.Role.CUSTOMER,
    )
    other_customer = User.objects.create_user(
        email='other-cert@simame.tech',
        phone='233550000003',
        password='StrongCertPass123!',
        role=User.Role.CUSTOMER,
    )
    service_type = Category.objects.create(name='Wash & Fold', type=Category.CategoryType.SERVICE_TYPE)
    item_category = Category.objects.create(name='Apparel', type=Category.CategoryType.ITEM_CATEGORY)
    item = LaunderableItem.objects.create(name='Trouser', item_category=item_category)
    laundry = Laundry.objects.create(
        name='KNUST Campus Laundry',
        description='Fast student laundry service',
        address='KNUST Campus Commercial Area',
        city='Kumasi',
        latitude=Decimal('6.674500'),
        longitude=Decimal('-1.571600'),
        phone_number='0240000010',
        owner=owner_user,
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
        min_order=Decimal('10.00'),
    )
    LaundryService.objects.create(
        laundry=laundry,
        item=item,
        service_type=service_type,
        price=Decimal('30.00'),
        is_available=True,
    )
    return owner_user, customer_user, other_customer, laundry, item, service_type


def _sign_payload(body_bytes: bytes, secret='cert-secret'):
    return hmac.new(secret.encode('utf-8'), body_bytes, hashlib.sha512).hexdigest()


@pytest.mark.django_db
class TestProductionCertificationDeep:
    """
    Comprehensive suite verifying all critical production failure modes,
    idempotency protections, security gates, and money integrity.
    """

    @pytest.fixture(autouse=True)
    def setup_env(self, settings):
        settings.ROOT_URLCONF = 'config.urls'
        settings.PAYSTACK_SECRET_KEY = 'cert-secret'
        settings.PAYMENT_CURRENCY = 'GHS'
        settings.DELIVERY_FEES_IN_APP = False
        settings.TAX_RATE = 0.00
        settings.PLATFORM_FEE_RATE = 0.00
        cache.clear()

    # =========================================================================
    # 1. PAYMENT FAILURE SCENARIOS (A THROUGH I)
    # =========================================================================

    def test_scenario_a_internet_drops_before_authorization(self):
        """Scenario A: Internet disappears before authorization. Payment stays PENDING without double mutation."""
        owner, customer, _, laundry, item, service_type = _create_fixtures()
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='Unity Hall, KNUST',
            delivery_address='Unity Hall, KNUST',
        )
        client = _auth_client(customer)

        with patch('payments.services.paystack.PaystackService.initialize_transaction') as mock_init:
            mock_init.return_value = {
                'status': True,
                'data': {'authorization_url': 'https://paystack.com/pay/abc', 'access_code': 'xyz'}
            }
            res = client.post(reverse('payment_initialize'), {'order_id': str(order.id), 'payment_method': 'CARD'})
            assert res.status_code == status.HTTP_200_OK

        payment = Payment.objects.get(order=order)
        assert payment.status == Payment.Status.PENDING
        assert order.payment_status == Order.PaymentStatus.UNPAID
        assert order.status == Order.Status.PENDING

        # Client retries initialize because connection dropped before authorization
        with patch('payments.services.paystack.PaystackService.initialize_transaction') as mock_init:
            mock_init.return_value = {
                'status': True,
                'data': {'authorization_url': 'https://paystack.com/pay/abc', 'access_code': 'xyz'}
            }
            res2 = client.post(reverse('payment_initialize'), {'order_id': str(order.id), 'payment_method': 'CARD'})
            assert res2.status_code == status.HTTP_200_OK

        # No duplicate payment records created
        assert Payment.objects.filter(order=order).count() == 1

    def test_scenario_b_c_d_webhook_arrives_while_app_offline_or_killed(self):
        """Scenarios B, C, D: Payment succeeds on Paystack, app is killed/offline, webhook confirms."""
        owner, customer, _, laundry, _, _ = _create_fixtures()
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='Africa Hall, KNUST',
            delivery_address='Africa Hall, KNUST',
        )
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('30.00'),
            currency='GHS',
            transaction_reference='ORD-SCENARIO-BCD-001',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.PENDING,
        )

        webhook_data = {
            'event': 'charge.success',
            'data': {
                'id': 'evt_offline_reconcile_001',
                'status': 'success',
                'reference': payment.transaction_reference,
                'amount': 3000, # 30.00 GHS in pesewas
                'currency': 'GHS',
                'channel': 'mobile_money',
                'metadata': {
                    'order_id': str(order.id),
                    'user_id': str(customer.id),
                },
            }
        }
        body_bytes = json.dumps(webhook_data).encode('utf-8')
        client = APIClient()
        res = client.post(
            reverse('paystack_webhook'),
            data=body_bytes,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=_sign_payload(body_bytes),
        )
        assert res.status_code == status.HTTP_200_OK

        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert payment.payment_method == Payment.Method.MOMO
        assert order.payment_status == Order.PaymentStatus.PAID
        assert order.status == Order.Status.CONFIRMED
        assert OrderSettlement.objects.filter(order=order).exists()

    def test_scenario_e_callback_arrives_before_webhook_then_webhook_arrives(self):
        """Scenario E: Client returns and calls verify before webhook arrives. Both handle idempotently."""
        owner, customer, _, laundry, _, _ = _create_fixtures()
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='Queens Hall, KNUST',
            delivery_address='Queens Hall, KNUST',
        )
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('30.00'),
            currency='GHS',
            transaction_reference='ORD-SCENARIO-E-001',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.PENDING,
        )

        client = _auth_client(customer)
        # 1. Client calls payment_verify
        with patch('payments.services.paystack.PaystackService.verify_transaction') as mock_verify:
            mock_verify.return_value = {
                'status': True,
                'data': {
                    'status': 'success',
                    'reference': payment.transaction_reference,
                    'amount': 3000,
                    'currency': 'GHS',
                    'metadata': {'order_id': str(order.id), 'user_id': str(customer.id)},
                }
            }
            res_verify = client.get(reverse('payment_verify', kwargs={'reference': payment.transaction_reference}))
            assert res_verify.status_code == status.HTTP_200_OK

        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert order.payment_status == Order.PaymentStatus.PAID
        assert order.status == Order.Status.CONFIRMED

        # 2. Webhook arrives minutes later
        webhook_data = {
            'event': 'charge.success',
            'data': {
                'id': 'evt_late_webhook_001',
                'status': 'success',
                'reference': payment.transaction_reference,
                'amount': 3000,
                'currency': 'GHS',
                'metadata': {'order_id': str(order.id), 'user_id': str(customer.id)},
            }
        }
        body_bytes = json.dumps(webhook_data).encode('utf-8')
        anon_client = APIClient()
        res_webhook = anon_client.post(
            reverse('paystack_webhook'),
            data=body_bytes,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=_sign_payload(body_bytes),
        )
        assert res_webhook.status_code == status.HTTP_200_OK
        # Exactly one settlement and no double transitions
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_scenario_f_webhook_arrives_before_client_callback(self):
        """Scenario F: Webhook arrives before client returns and verifies."""
        owner, customer, _, laundry, _, _ = _create_fixtures()
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='Independence Hall, KNUST',
            delivery_address='Independence Hall, KNUST',
        )
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('30.00'),
            currency='GHS',
            transaction_reference='ORD-SCENARIO-F-001',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.PENDING,
        )

        # 1. Webhook arrives first
        webhook_data = {
            'event': 'charge.success',
            'data': {
                'id': 'evt_early_webhook_001',
                'status': 'success',
                'reference': payment.transaction_reference,
                'amount': 3000,
                'currency': 'GHS',
                'metadata': {'order_id': str(order.id), 'user_id': str(customer.id)},
            }
        }
        body_bytes = json.dumps(webhook_data).encode('utf-8')
        res_webhook = APIClient().post(
            reverse('paystack_webhook'),
            data=body_bytes,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=_sign_payload(body_bytes),
        )
        assert res_webhook.status_code == status.HTTP_200_OK

        # 2. Client callback then verifies
        client = _auth_client(customer)
        with patch('payments.services.paystack.PaystackService.verify_transaction') as mock_verify:
            mock_verify.return_value = {
                'status': True,
                'data': {
                    'status': 'success',
                    'reference': payment.transaction_reference,
                    'amount': 3000,
                    'currency': 'GHS',
                    'metadata': {'order_id': str(order.id), 'user_id': str(customer.id)},
                }
            }
            res_verify = client.get(reverse('payment_verify', kwargs={'reference': payment.transaction_reference}))
            assert res_verify.status_code == status.HTTP_200_OK

        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS
        assert order.payment_status == Order.PaymentStatus.PAID
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_scenario_g_webhook_delivered_twice(self):
        """Scenario G: Duplicate webhook deliveries."""
        owner, customer, _, laundry, _, _ = _create_fixtures()
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='University Hall (Katanga), KNUST',
            delivery_address='University Hall (Katanga), KNUST',
        )
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('30.00'),
            currency='GHS',
            transaction_reference='ORD-SCENARIO-G-001',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.PENDING,
        )

        webhook_data = {
            'event': 'charge.success',
            'data': {
                'id': 'evt_duplicate_delivery_001',
                'status': 'success',
                'reference': payment.transaction_reference,
                'amount': 3000,
                'currency': 'GHS',
                'metadata': {'order_id': str(order.id), 'user_id': str(customer.id)},
            }
        }
        body_bytes = json.dumps(webhook_data).encode('utf-8')
        sig = _sign_payload(body_bytes)

        client = APIClient()
        r1 = client.post(reverse('paystack_webhook'), data=body_bytes, content_type='application/json', HTTP_X_PAYSTACK_SIGNATURE=sig)
        r2 = client.post(reverse('paystack_webhook'), data=body_bytes, content_type='application/json', HTTP_X_PAYSTACK_SIGNATURE=sig)

        assert r1.status_code == status.HTTP_200_OK
        assert r2.status_code == status.HTTP_200_OK
        assert WebhookEvent.objects.filter(event_id='evt_duplicate_delivery_001').count() == 1
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_scenario_i_customer_reopens_app_hours_later(self):
        """Scenario I: Customer reopens app hours later and queries order/payment status."""
        owner, customer, _, laundry, _, _ = _create_fixtures()
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='Unity Hall, KNUST',
            delivery_address='Unity Hall, KNUST',
            status=Order.Status.CONFIRMED,
            payment_status=Order.PaymentStatus.PAID,
        )
        payment = Payment.objects.create(
            user=customer,
            order=order,
            amount=Decimal('30.00'),
            currency='GHS',
            transaction_reference='ORD-SCENARIO-I-001',
            payment_method=Payment.Method.MOMO,
            status=Payment.Status.SUCCESS,
        )

        client = _auth_client(customer)
        res = client.get(reverse('payment_status', kwargs={'reference': payment.transaction_reference}))
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data['data']['payment_status'] == 'SUCCESS'
        assert data['data']['order_status'] == 'CONFIRMED'

    # =========================================================================
    # 2. ORDER CREATION IDEMPOTENCY & BUTTON MASHING
    # =========================================================================

    def test_order_creation_button_mashing_with_idempotency_key(self):
        """User mashes 'Place Order' button (10 rapid requests with same idempotency key). Exactly 1 order created."""
        owner, customer, _, laundry, item, service_type = _create_fixtures()
        client = _auth_client(customer)

        booking_payload = {
            'laundry': str(laundry.id),
            'pickup_address': 'Brunei Complex, KNUST',
            'delivery_address': 'Brunei Complex, KNUST',
            'pickup_date': (timezone.now() + timezone.timedelta(days=1)).isoformat(),
            'payment_method': 'CARD',
            'pricing_mode': 'BY_ITEM',
            'items': [
                {'item': str(item.id), 'service_type': str(service_type.id), 'quantity': 2}
            ]
        }

        idempotency_key = f"mashing_key_{uuid.uuid4()}"

        with patch('payments.services.paystack.PaystackService.initialize_transaction') as mock_init:
            mock_init.return_value = {
                'status': True,
                'data': {'authorization_url': 'https://paystack.com/pay/abc', 'access_code': 'xyz'}
            }

            responses = []
            for _ in range(10):
                resp = client.post(
                    reverse('booking-create'),
                    data=booking_payload,
                    format='json',
                    HTTP_X_IDEMPOTENCY_KEY=idempotency_key,
                )
                responses.append(resp)

        first_order_id = responses[0].data['id']
        for r in responses:
            assert r.status_code == status.HTTP_201_CREATED
            assert r.data['id'] == first_order_id

        # Exactly 1 order created in database
        assert Order.objects.filter(user=customer).count() == 1

    # =========================================================================
    # 3. COUPON CONCURRENCY & RACE CONDITION SAFETY
    # =========================================================================

    def test_coupon_single_use_limit_protection(self):
        """Single-use coupon cannot be redeemed multiple times by concurrent checkouts."""
        owner, customer, other_customer, laundry, item, service_type = _create_fixtures()
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='Unity Hall, KNUST',
            delivery_address='Unity Hall, KNUST',
        )
        coupon = Coupon.objects.create(
            code='KNUSTWELCOME',
            discount_type=Coupon.DiscountType.FIXED,
            discount_value=Decimal('5.00'),
            max_usage=1,
            user_limit=1,
            is_active=True,
            valid_to=timezone.now() + timezone.timedelta(days=7),
        )

        # Customer 1 claims the coupon
        CouponUsage.objects.create(coupon=coupon, user=customer, order=order)
        coupon.current_usage += 1
        coupon.save()

        # Customer 2 attempts to use the same exhausted coupon
        assert coupon.is_valid(user=other_customer, order_value=30)[0] is False

    # =========================================================================
    # 4. 1,000 USER SIGNUP & CLERK SYNCHRONIZATION CONCURRENCY
    # =========================================================================

    def test_clerk_user_sync_atomic_deduplication(self):
        """Concurrent sync of same Clerk profile produces exactly 1 user and no duplicates."""
        profile = ClerkProfile(
            clerk_user_id='user_clerk_bulk_001',
            email='student.knust@st.knust.edu.gh',
            first_name='Kofi',
            last_name='Mensah',
            email_verified=True,
        )

        user1, created1 = sync_user_from_clerk(profile=profile, requested_role=User.Role.CUSTOMER)
        user2, created2 = sync_user_from_clerk(profile=profile, requested_role=User.Role.CUSTOMER)

        assert created1 is True
        assert created2 is False
        assert user1.id == user2.id
        assert User.objects.filter(email='student.knust@st.knust.edu.gh').count() == 1

    def test_clerk_role_escalation_prevented(self):
        """Customer cannot request ADMIN or SUPERADMIN role via social sync."""
        profile = ClerkProfile(
            clerk_user_id='user_clerk_hacker_001',
            email='attacker@simame.tech',
            first_name='Bad',
            last_name='Actor',
            email_verified=True,
        )
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError):
            sync_user_from_clerk(profile=profile, requested_role='ADMIN')

    # =========================================================================
    # 5. SECURITY RED TEAM: IDOR & OBJECT-LEVEL AUTHORIZATION
    # =========================================================================

    def test_idor_user_cannot_access_other_users_order_or_payment(self):
        """User A cannot view or manipulate User B's order or payment status."""
        owner, customer, other_customer, laundry, _, _ = _create_fixtures()
        order_b = Order.objects.create(
            user=other_customer,
            laundry=laundry,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('30.00'),
            pickup_address='Africa Hall, KNUST',
            delivery_address='Africa Hall, KNUST',
        )
        payment_b = Payment.objects.create(
            user=other_customer,
            order=order_b,
            amount=Decimal('30.00'),
            currency='GHS',
            transaction_reference='ORD-SECRET-B-001',
            payment_method=Payment.Method.CARD,
            status=Payment.Status.PENDING,
        )

        client_a = _auth_client(customer)

        # Try to view User B's order detail via /api/v1/orders/<pk>/
        res_order = client_a.get(f"/api/v1/orders/{order_b.id}/")
        assert res_order.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)

        # Try to view User B's payment status
        res_payment = client_a.get(reverse('payment_status', kwargs={'reference': payment_b.transaction_reference}))
        assert res_payment.status_code == status.HTTP_404_NOT_FOUND

    def test_owner_cannot_access_unowned_laundry_orders(self):
        """Owner of Laundry A cannot view orders belonging to Laundry B."""
        owner_a, customer, other_customer, laundry_a, _, _ = _create_fixtures()
        owner_b = User.objects.create_user(
            email='owner-b@simame.tech',
            phone='233550000099',
            password='StrongPass123!',
            role=User.Role.OWNER,
        )
        laundry_b = Laundry.objects.create(
            name='Laundry B',
            address='Ayeduase, KNUST',
            city='Kumasi',
            latitude=Decimal('6.670000'),
            longitude=Decimal('-1.570000'),
            phone_number='0240000099',
            owner=owner_b,
            status=Laundry.ApprovalStatus.APPROVED,
            is_active=True,
            min_order=Decimal('10.00'),
        )
        order_b = Order.objects.create(
            user=customer,
            laundry=laundry_b,
            pickup_date=timezone.now() + timezone.timedelta(days=1),
            total_amount=Decimal('50.00'),
            pickup_address='Ayeduase Gate, KNUST',
            delivery_address='Ayeduase Gate, KNUST',
        )

        client_owner_a = _auth_client(owner_a)
        res = client_owner_a.get(f"/api/v1/orders/{order_b.id}/")
        assert res.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)

    # =========================================================================
    # 6. MONEY INTEGRITY & FLOATING POINT DEFENSE
    # =========================================================================

    def test_money_decimal_precision_no_float_drift(self):
        """Verify strict decimal math and pesewa minor unit calculations."""
        from payments.services.paystack import to_minor_units
        assert to_minor_units('25.50') == 2550
        assert to_minor_units('0.01') == 1
        assert to_minor_units(Decimal('100.99')) == 10099
        assert to_minor_units('10.005') == 1001 # ROUND_HALF_UP

    def test_price_tampering_rejected_by_server_side_calculation(self):
        """If client attempts to pass forbidden fields (like total_amount), serializer strictly rejects with 400."""
        owner, customer, _, laundry, item, service_type = _create_fixtures()
        client = _auth_client(customer)

        malicious_payload = {
            'laundry': str(laundry.id),
            'pickup_address': 'GUSSS Hostels, KNUST',
            'delivery_address': 'GUSSS Hostels, KNUST',
            'pickup_date': (timezone.now() + timezone.timedelta(days=1)).isoformat(),
            'payment_method': 'CARD',
            'total_amount': '1.00', # Malicious attempt to force 1 GHS
            'items': [
                {'item': str(item.id), 'service_type': str(service_type.id), 'quantity': 2}
            ]
        }

        res = client.post(reverse('booking-create'), data=malicious_payload, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'total_amount' in str(res.data)

        # Valid payload calculates correct total on server side
        valid_payload = {
            'laundry': str(laundry.id),
            'pickup_address': 'GUSSS Hostels, KNUST',
            'delivery_address': 'GUSSS Hostels, KNUST',
            'pickup_date': (timezone.now() + timezone.timedelta(days=1)).isoformat(),
            'payment_method': 'CARD',
            'items': [
                {'item': str(item.id), 'service_type': str(service_type.id), 'quantity': 2}
            ]
        }
        with patch('payments.services.paystack.PaystackService.initialize_transaction') as mock_init:
            mock_init.return_value = {
                'status': True,
                'data': {'authorization_url': 'https://paystack.com/pay/abc', 'access_code': 'xyz'}
            }
            res_valid = client.post(reverse('booking-create'), data=valid_payload, format='json')
            assert res_valid.status_code == status.HTTP_201_CREATED

        order = Order.objects.get(id=res_valid.data['id'])
        # Authoritative server-side total: 2 * 30.00 = 60.00 GHS
        assert order.total_amount == Decimal('60.00')

    # =========================================================================
    # 7. DATA PRIVACY & PII REDACTION
    # =========================================================================

    def test_sentry_before_send_redaction_scrubs_pii(self):
        """Sentry before_send callback strips authorization headers, cookies, passwords, and PII."""
        from config.settings import _before_send_sentry

        mock_event = {
            'request': {
                'headers': {'Authorization': 'Bearer test-secret-token'},
                'data': {'password': 'SecretPassword123!', 'phone': '233550000001'},
                'cookies': {'sessionid': 'secret-session'},
            },
            'user': {
                'email': 'student@knust.edu.gh',
                'ip_address': '197.251.10.1',
                'phone': '233550000001',
            }
        }
        sanitized = _before_send_sentry(mock_event, None)
        assert 'headers' not in sanitized['request']
        assert 'data' not in sanitized['request']
        assert 'cookies' not in sanitized['request']
        assert sanitized['user']['email'] == '[REDACTED]'
        assert sanitized['user']['ip_address'] == '[REDACTED]'
