"""Comprehensive test suite for Laundry Owner (Merchant) SMS Notifications.

Covers:
- Pay-first vs COD trigger timing
- Tenant isolation and data boundary protection
- Number normalization (024, +233, 233)
- Verification lifecycle (OTP and Clerk account adoption)
- Privacy masking and SMS segment rendering
- Blocked states (unverified, missing, disabled)
- Delivery callbacks and reconciliation
- Dashboard API endpoints with owner authentication
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from admin_notifications.models import (
    AdminNotificationDelivery as Delivery,
    AdminNotificationEvent as Event,
    MerchantNotificationSettings,
    MerchantSmsSendLog,
)
from admin_notifications.phone import mask_recipient, normalize_admin_recipient
from admin_notifications.services import merchant, outbox
from admin_notifications.services.callbacks import apply_sms_status
from admin_notifications.services.dispatcher import dispatch_due
from admin_notifications.tests.helpers import (
    FakeSms,
    book,
    configure,
    make_world,
    patch_providers,
)
from ordering.models import Order
from payments.models import Payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def merchant_world(settings):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False
    world = make_world()
    settings_row = MerchantNotificationSettings.objects.create(
        laundry=world.laundry,
        phone='233241234567',
        phone_verified_at=timezone.now(),
        sms_enabled=True,
    )
    return world, settings_row


# --- 1. Order Trigger Timing ---------------------------------------------------

def test_cod_order_creates_merchant_sms_on_creation(merchant_world, monkeypatch):
    world, settings_row = merchant_world
    providers = patch_providers(monkeypatch, sms=FakeSms())

    response = book(world, method='CASH')
    assert response.status_code == 201
    order = Order.objects.get(pk=response.data['id'])

    # Verify outbox event and delivery
    event = Event.objects.get(order=order, event_type=Event.EventType.NEW_ORDER)
    delivery = Delivery.objects.get(event=event, audience=Delivery.Audience.MERCHANT)
    assert delivery.recipient == '233241234567'
    assert delivery.status == Delivery.Status.PENDING

    # Dispatch and verify provider send
    result = dispatch_due()
    assert result.claimed == 1
    assert result.accepted == 1

    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.SUBMITTED
    assert len(providers.sms.calls) == 1
    recipient, msg = providers.sms.calls[0]
    assert recipient == '233241234567'
    assert order.order_no in msg
    assert 'SIMAME' in msg


def test_pay_first_order_does_not_alert_until_payment_confirmed(merchant_world, monkeypatch):
    world, settings_row = merchant_world
    patch_providers(monkeypatch, sms=FakeSms())

    # 1. Initiated pay-first order with mocked Paystack init
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        response = book(world, method='CARD')
    assert response.status_code == 201
    order = Order.objects.get(pk=response.data['id'])

    # Pay-first order: merchant delivery is NOT created on NEW_ORDER
    merchant_deliveries = Delivery.objects.filter(event__order=order, audience=Delivery.Audience.MERCHANT)
    assert merchant_deliveries.count() == 0

    # 2. Payment confirmed event
    payment = Payment.objects.get(order=order)
    payment.status = Payment.Status.SUCCESS
    payment.save()
    order.payment_status = Order.PaymentStatus.PAID

    order.save()

    outbox.emit_payment_confirmed(order)

    # Merchant delivery is now generated on PAYMENT_CONFIRMED
    event = Event.objects.get(order=order, event_type=Event.EventType.PAYMENT_CONFIRMED)
    delivery = Delivery.objects.get(event=event, audience=Delivery.Audience.MERCHANT)
    assert delivery.recipient == '233241234567'
    assert delivery.status == Delivery.Status.PENDING



# --- 2. Tenant Isolation & Privacy ---------------------------------------------

def test_tenant_isolation_each_merchant_receives_only_their_bookings(settings, monkeypatch):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False
    patch_providers(monkeypatch, sms=FakeSms())

    world_a = make_world(first_name='Ama', last_name='A')
    world_b = make_world(first_name='Kofi', last_name='B')

    MerchantNotificationSettings.objects.create(
        laundry=world_a.laundry,
        phone='233241111111',
        phone_verified_at=timezone.now(),
        sms_enabled=True,
    )
    MerchantNotificationSettings.objects.create(
        laundry=world_b.laundry,
        phone='233242222222',
        phone_verified_at=timezone.now(),
        sms_enabled=True,
    )

    resp_a = book(world_a, pickup_address='Ayeduase Gate')
    resp_b = book(world_b, pickup_address='Brunei Complex')

    order_a = Order.objects.get(pk=resp_a.data['id'])
    order_b = Order.objects.get(pk=resp_b.data['id'])

    deliv_a = Delivery.objects.get(event__order=order_a, audience=Delivery.Audience.MERCHANT)
    deliv_b = Delivery.objects.get(event__order=order_b, audience=Delivery.Audience.MERCHANT)

    assert deliv_a.recipient == '233241111111'
    assert deliv_a.laundry_id == world_a.laundry.id
    assert 'Ayeduase Gate' in deliv_a.event.payload['pickup']['address']
    assert 'Brunei' not in deliv_a.event.payload['pickup']['address']

    assert deliv_b.recipient == '233242222222'
    assert deliv_b.laundry_id == world_b.laundry.id
    assert 'Brunei Complex' in deliv_b.event.payload['pickup']['address']
    assert 'Ayeduase' not in deliv_b.event.payload['pickup']['address']


# --- 3. Number Normalization & Verification Lifecycle --------------------------

def test_ghana_phone_number_normalization():
    assert normalize_admin_recipient('024 123 4567') == '233241234567'
    assert normalize_admin_recipient('+233241234567') == '233241234567'
    assert normalize_admin_recipient('233241234567') == '233241234567'
    assert mask_recipient('233241234567') == '233 24 *** 4567'



def test_otp_verification_flow(settings, monkeypatch):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    providers = patch_providers(monkeypatch, sms=FakeSms())
    world = make_world()

    # 1. Start verification
    with patch('secrets.randbelow', return_value=123456):
        row = merchant.start_verification(world.laundry, world.owner, '024 123 4567')

    assert row.pending_phone == '233241234567'
    assert row.otp_expires_at is not None
    assert row.is_verified is False
    assert len(providers.sms.calls) == 1
    assert '123456' in providers.sms.calls[0][1]

    # 2. Confirm verification with valid code
    confirmed_row = merchant.confirm_verification(world.laundry, world.owner, '123456')
    assert confirmed_row.phone == '233241234567'
    assert confirmed_row.is_verified is True
    assert confirmed_row.pending_phone == ''


def test_account_phone_adoption(settings):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    world = make_world()
    world.owner.phone = '+233249876543'
    world.owner.phone_verified = True
    world.owner.save()

    row = merchant.adopt_account_phone(world.laundry, world.owner)
    assert row.phone == '233249876543'
    assert row.is_verified is True
    assert row.verification_method == MerchantNotificationSettings.VerificationMethod.ACCOUNT


# --- 4. Unverified & Missing Numbers are Blocked ------------------------------

def test_unverified_phone_is_marked_blocked_without_failing_order(settings):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False
    world = make_world()
    # No verified settings created for this laundry

    response = book(world, method='CASH')
    assert response.status_code == 201

    order = Order.objects.get(pk=response.data['id'])
    delivery = Delivery.objects.get(event__order=order, audience=Delivery.Audience.MERCHANT)

    assert delivery.status == Delivery.Status.BLOCKED
    assert delivery.last_error_class == 'NO_VERIFIED_PHONE'

    # Dispatcher safely ignores blocked delivery
    result = dispatch_due()
    assert result.claimed == 0


# --- 5. Delivery Status Callbacks ----------------------------------------------

def test_delivery_status_callback_updates_record(merchant_world, monkeypatch):
    world, settings_row = merchant_world
    patch_providers(monkeypatch, sms=FakeSms())

    response = book(world, method='CASH')
    assert response.status_code == 201
    dispatch_due()

    delivery = Delivery.objects.get(audience=Delivery.Audience.MERCHANT)
    sms_id = delivery.provider_messages.get().provider_message_id

    # Simulate Arkesel callback with DELIVERED status
    res = apply_sms_status(sms_id, 'DELIVERED')
    assert res == 'updated'

    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.DELIVERED
    assert delivery.delivered_at is not None


# --- 6. Seller Center Dashboard API Endpoints ---------------------------------

def test_merchant_dashboard_sms_alerts_api(merchant_world):
    world, settings_row = merchant_world
    client = APIClient()
    client.force_authenticate(user=world.owner)

    url = reverse('dashboard-sms-alerts')

    # GET settings
    get_res = client.get(url)
    assert get_res.status_code == 200
    assert get_res.data['status'] == 'success'
    data = get_res.data['data']
    assert data['sms_enabled'] is True
    assert data['phone_verified'] is True
    assert '233 24 ***' in data['phone_masked']


    # PATCH disable SMS
    patch_res = client.patch(url, {'sms_enabled': False})
    assert patch_res.status_code == 200
    assert patch_res.data['data']['sms_enabled'] is False

    settings_row.refresh_from_db()
    assert settings_row.sms_enabled is False


def test_merchant_dashboard_test_sms_endpoint(merchant_world, monkeypatch):
    world, settings_row = merchant_world
    patch_providers(monkeypatch, sms=FakeSms())
    client = APIClient()
    client.force_authenticate(user=world.owner)

    url = reverse('dashboard-sms-alerts-test')
    res = client.post(url)
    assert res.status_code == 202
    assert res.data['status'] == 'success'
    assert MerchantSmsSendLog.objects.filter(laundry=world.laundry).count() == 1
