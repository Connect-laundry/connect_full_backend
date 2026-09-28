"""Shared fixtures for admin notification tests."""
import threading
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from admin_notifications.providers import base

WA_ADMINS = ['0551057139', '0200031713', '0541604786']
WA_NORMALIZED = ['233551057139', '233200031713', '233541604786']
SMS_ADMIN = '0551057139'
SMS_NORMALIZED = '233551057139'
CALLBACK_SECRET = 'cb-secret-0123456789abcdefghijklmnop'


def configure(settings, *, sms=True, whatsapp=True, environment='production'):
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_SMS_NOTIFICATIONS_ENABLED = sms
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = whatsapp
    settings.ADMIN_WHATSAPP_RECIPIENTS = ','.join(WA_ADMINS)
    settings.ADMIN_SMS_RECIPIENTS = SMS_ADMIN
    settings.ADMIN_NOTIFICATION_ENVIRONMENT = environment
    settings.ADMIN_BASE_URL = 'https://admin.simame-ops.com'
    settings.ADMIN_NOTIFICATION_ADMIN_BASE_URL = 'https://admin.simame-ops.com'
    settings.ARKESEL_API_KEY = 'ark-test-key-DO-NOT-LEAK'
    settings.ARKESEL_SENDER_ID = 'SIMAME'
    settings.ARKESEL_SMS_SANDBOX = 'false' if environment == 'production' else 'true'
    settings.ARKESEL_SMS_CALLBACK_SECRET = CALLBACK_SECRET
    settings.ARKESEL_SMS_CALLBACK_BASE_URL = 'https://api.simame-ops.com'
    settings.ARKESEL_WHATSAPP_API_TOKEN = 'wa-token-DO-NOT-LEAK'
    settings.ARKESEL_WHATSAPP_ORGANIZATION_ID = 'org_simame'
    settings.ARKESEL_WHATSAPP_INBOX_ID = 'inbox-uuid-1'
    settings.ARKESEL_WHATSAPP_NEW_ORDER_TEMPLATE_ID = 'tmpl-new-order'
    settings.ARKESEL_WHATSAPP_ORDER_UPDATE_TEMPLATE_ID = 'tmpl-order-update'
    settings.ADMIN_NOTIFICATION_DISPATCH_IN_THREAD = False
    settings.ADMIN_NOTIFICATION_SWEEP_ENABLED = False


@dataclass
class World:
    owner: object
    customer: object
    laundry: object
    service: object
    service2: object
    weight_pricing: object


def make_world(*, first_name='Ama', last_name='Mensah', service_price='10.00'):
    from laundries.models.category import Category
    from laundries.models.laundry import Laundry
    from laundries.models.pricing import LaundryWeightPricing
    from laundries.models.service import LaundryService
    from ordering.models import LaunderableItem
    from users.models import User

    suffix = uuid.uuid4().hex[:6]
    digits = str(int(suffix, 16) % 10 ** 7).zfill(7)
    owner = User.objects.create_user(email=f'owner-{suffix}@example.com', phone=f'024{digits}',
                                     password='StrongPass123!', role=User.Role.OWNER)
    customer = User.objects.create_user(email=f'cust-{suffix}@example.com', phone=f'055{digits}',
                                        password='StrongPass123!', first_name=first_name, last_name=last_name)
    wash = Category.objects.create(name=f'Wash & Iron {suffix}', type=Category.CategoryType.SERVICE_TYPE)
    cat = Category.objects.create(name=f'Cat {suffix}', type=Category.CategoryType.ITEM_CATEGORY)
    shirt = LaunderableItem.objects.create(name=f'Shirt Wash {suffix}', item_category=cat)
    duvet = LaunderableItem.objects.create(name=f'Duvet King {suffix}', item_category=cat)
    laundry = Laundry.objects.create(
        name=f'Adepa Laundry {suffix}', description='test', address='12 Ring Road, Adum', city='Kumasi',
        latitude='5.6037', longitude='-0.1870', phone_number='0241112233', owner=owner,
        status=Laundry.ApprovalStatus.APPROVED, is_active=True,
    )
    service = LaundryService.objects.create(laundry=laundry, item=shirt, service_type=wash,
                                            price=Decimal(service_price), is_available=True)
    service2 = LaundryService.objects.create(laundry=laundry, item=duvet, service_type=wash,
                                             price=Decimal('45.00'), is_available=True)
    weight = LaundryWeightPricing.objects.create(laundry=laundry, base_price_per_kg=Decimal('20.00'),
                                                 minimum_charge=Decimal('0.00'), is_active=True)
    return World(owner, customer, laundry, service, service2, weight)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def book(world, *, method='CASH', mode='BY_ITEM', items=None, weight=None, notes='Gate is blue, call on arrival',
         pickup_address='KNUST Ayeduase, near Kotei junction', delivery_address=None, coords=True,
         pickup_date=None, headers=None):
    body = {
        'laundry': str(world.laundry.id),
        'pickup_date': (pickup_date or (timezone.now() + timedelta(days=1)).replace(
            hour=17, minute=0, second=0, microsecond=0)).isoformat(),
        'pickup_address': pickup_address,
        'delivery_address': delivery_address or pickup_address,
        'payment_method': method,
        'pricing_mode': mode,
        'special_instructions': notes,
    }
    if coords:
        body.update(pickup_lat='5.6040000', pickup_lng='-0.1860000')
        body.update(delivery_lat='5.6040000', delivery_lng='-0.1860000')
    if mode == 'BY_ITEM':
        body['items'] = items if items is not None else [
            {'item': str(world.service.item_id), 'service_type': str(world.service.service_type_id), 'quantity': 2},
            {'item': str(world.service2.item_id), 'service_type': str(world.service2.service_type_id),
             'quantity': 1},
        ]
    if mode == 'BY_WEIGHT':
        body['estimated_weight_kg'] = weight or '4.00'
    return client_for(world.customer).post(reverse('booking-create'), body, format='json', **(headers or {}))


class FakeSms:
    """Records every send; returns scripted results per recipient/call."""

    def __init__(self, script=None, delay=0.0):
        self.calls = []
        self.lock = threading.Lock()
        self.script = script or (lambda recipient, message, n: base.SendResult(
            base.ACCEPTED, provider_message_id=f'sms-{uuid.uuid4().hex[:12]}'))
        self.delay = delay

    def send(self, recipient, message):
        import time
        with self.lock:
            n = len(self.calls)
            self.calls.append((recipient, message))
        if self.delay:
            time.sleep(self.delay)
        return self.script(recipient, message, n)


class FakeWhatsApp:
    def __init__(self, script=None, delay=0.0):
        self.calls = []
        self.lock = threading.Lock()
        self.script = script or (lambda recipient, key, params, n: base.SendResult(
            base.ACCEPTED, provider_message_id=f'wa-{uuid.uuid4().hex[:12]}'))
        self.delay = delay

    def send(self, recipient, template_key, params):
        import time
        with self.lock:
            n = len(self.calls)
            self.calls.append((recipient, template_key, tuple(params)))
        if self.delay:
            time.sleep(self.delay)
        return self.script(recipient, template_key, params, n)


@dataclass
class Providers:
    sms: FakeSms = field(default_factory=FakeSms)
    whatsapp: FakeWhatsApp = field(default_factory=FakeWhatsApp)


def patch_providers(monkeypatch, sms=None, whatsapp=None) -> Providers:
    providers = Providers(sms or FakeSms(), whatsapp or FakeWhatsApp())
    monkeypatch.setattr('admin_notifications.services.dispatcher.get_sms_provider', lambda: providers.sms)
    monkeypatch.setattr('admin_notifications.services.dispatcher.get_whatsapp_provider', lambda: providers.whatsapp)
    return providers
