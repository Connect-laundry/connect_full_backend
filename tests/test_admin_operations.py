"""Tests for the Admin Operations Center: global search, admin notification
feed, audit log, the unified NotificationService, and event triggers.
"""
import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from users.models import User
from laundries.models.laundry import Laundry
from ordering.models import Order
from payments.models import Payment
from marketplace.models import Notification, AuditLog
from marketplace.services.notification_service import NotificationService


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def platform_admin(db):
    return User.objects.create_user(
        email='ops-admin@example.com', phone='233400000000',
        password='StrongPass123!', role=User.Role.ADMIN, is_staff=True,
    )


@pytest.fixture
def admin_client(platform_admin):
    client = APIClient()
    client.force_authenticate(user=platform_admin)
    return client


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        email='shopper@example.com', phone='233411111111', password='StrongPass123!',
    )


def _admin_notifs(category=None):
    qs = Notification.objects.filter(audience=Notification.Audience.ADMIN)
    if category:
        qs = qs.filter(category=category)
    return qs


# --------------------------------------------------------------------------- #
# Global search
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestAdminSearch:
    def test_requires_authentication(self):
        resp = APIClient().get(reverse('admin_search'), {'q': 'abc'})
        assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_customer_forbidden(self, customer):
        client = APIClient()
        client.force_authenticate(user=customer)
        resp = client.get(reverse('admin_search'), {'q': 'abc'})
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_short_query_returns_empty(self, admin_client):
        resp = admin_client.get(reverse('admin_search'), {'q': 'a'})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['data']['total'] == 0

    def test_finds_user_by_partial_email_and_phone(self, admin_client, customer):
        resp = admin_client.get(reverse('admin_search'), {'q': 'shopper'})
        assert resp.status_code == status.HTTP_200_OK
        emails = [u['sublabel'] for u in resp.data['data']['users']]
        assert 'shopper@example.com' in emails

        resp2 = admin_client.get(reverse('admin_search'), {'q': '23341111'})
        assert any(u['id'] == str(customer.id) for u in resp2.data['data']['users'])

    def test_finds_order_laundry_payment_coupon(self, admin_client, customer):
        owner = User.objects.create_user(
            email='o@example.com', phone='233422222222', password='x', role=User.Role.OWNER)
        laundry = Laundry.objects.create(
            name='Sparkle Wash', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000000')
        order = Order.objects.create(
            user=customer, laundry=laundry, status='PENDING',
            total_amount=50, pickup_date=timezone.now())
        payment = Payment.objects.create(
            user=customer, order=order, amount=50, currency='GHS',
            transaction_reference='TXNSEARCH123', status=Payment.Status.PENDING)
        from ordering.models.coupons import Coupon
        Coupon.objects.create(code='SPARKLE20', discount_type=Coupon.DiscountType.FIXED,
                              discount_value=20)

        # Order by order_no
        r = admin_client.get(reverse('admin_search'), {'q': order.order_no})
        assert any(o['id'] == str(order.id) for o in r.data['data']['orders'])
        # Laundry by name
        r = admin_client.get(reverse('admin_search'), {'q': 'Sparkle'})
        assert any(l['id'] == str(laundry.id) for l in r.data['data']['laundries'])
        # Payment by reference
        r = admin_client.get(reverse('admin_search'), {'q': 'TXNSEARCH'})
        assert any(p['id'] == str(payment.id) for p in r.data['data']['payments'])
        # Coupon by code
        r = admin_client.get(reverse('admin_search'), {'q': 'SPARKLE2'})
        assert any(c['label'] == 'SPARKLE20' for c in r.data['data']['coupons'])

    def test_search_writes_audit_log(self, admin_client):
        admin_client.get(reverse('admin_search'), {'q': 'shopper'})
        assert AuditLog.objects.filter(action=AuditLog.Action.ADMIN_SEARCH).exists()


# --------------------------------------------------------------------------- #
# NotificationService
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestNotificationService:
    def test_notify_user_creates_user_notification(self, customer):
        n = NotificationService.notify_user(customer, 'Hi', 'Body', category='TEST')
        assert n.audience == Notification.Audience.USER
        assert n.user == customer

    def test_notify_admins_creates_admin_broadcast(self):
        n = NotificationService.notify_admins('Heads up', 'Body', category='SYSTEM_ERROR')
        assert n.audience == Notification.Audience.ADMIN
        assert n.user is None

    def test_dedup_prevents_duplicates(self, customer):
        a = NotificationService.notify_user(customer, 'X', 'Y', dedup_key='k1')
        b = NotificationService.notify_user(customer, 'X', 'Y', dedup_key='k1')
        assert a.id == b.id
        assert Notification.objects.filter(dedup_key='k1').count() == 1


# --------------------------------------------------------------------------- #
# Admin notification feed
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestAdminNotificationFeed:
    def test_list_unread_and_mark_read(self, admin_client):
        n = NotificationService.notify_admins('A', 'B', category='SYSTEM_ERROR')

        r = admin_client.get(reverse('admin_notifications'))
        assert r.status_code == status.HTTP_200_OK
        assert any(item['id'] == str(n.id) for item in r.data['data']['results'])

        before = admin_client.get(reverse('admin_notifications_unread')).data['data']['unread']
        assert before >= 1

        mr = admin_client.post(reverse('admin_notification_read', kwargs={'pk': n.id}))
        assert mr.status_code == status.HTTP_200_OK
        n.refresh_from_db()
        assert n.is_read is True

    def test_mark_all_read(self, admin_client):
        NotificationService.notify_admins('A', 'B', category='C1')
        NotificationService.notify_admins('A2', 'B2', category='C2')
        resp = admin_client.post(reverse('admin_notifications_read_all'))
        assert resp.status_code == status.HTTP_200_OK
        assert _admin_notifs().filter(is_read=False).count() == 0

    def test_category_filter(self, admin_client):
        NotificationService.notify_admins('A', 'B', category='ONLYME')
        r = admin_client.get(reverse('admin_notifications'), {'category': 'ONLYME'})
        assert all(i['category'] == 'ONLYME' for i in r.data['data']['results'])
        assert len(r.data['data']['results']) >= 1

    def test_customer_cannot_access_admin_feed(self, customer):
        client = APIClient()
        client.force_authenticate(user=customer)
        assert client.get(reverse('admin_notifications')).status_code == status.HTTP_403_FORBIDDEN

    def test_admin_broadcast_not_visible_to_customer_feed(self, customer):
        NotificationService.notify_admins('secret', 'admin only', category='SEC')
        client = APIClient()
        client.force_authenticate(user=customer)
        # Customer notifications endpoint (support app)
        resp = client.get(reverse('notification-list'))
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        results = body.get('data', {}).get('results', body.get('results', []))
        assert all('admin only' not in (item.get('body') or '') for item in results)


# --------------------------------------------------------------------------- #
# Audit log API
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestAuditLogApi:
    def test_admin_only(self, customer):
        client = APIClient()
        client.force_authenticate(user=customer)
        assert client.get(reverse('admin_audit_log')).status_code == status.HTTP_403_FORBIDDEN

    def test_filter_by_action(self, admin_client):
        admin_client.get(reverse('admin_search'), {'q': 'anything'})
        resp = admin_client.get(reverse('admin_audit_log'), {'action': 'ADMIN_SEARCH'})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['data']['total'] >= 1
        assert all(r['action'] == 'ADMIN_SEARCH' for r in resp.data['data']['results'])


# --------------------------------------------------------------------------- #
# Event triggers (signals)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestEventTriggers:
    def test_new_owner_registration_notifies_admins(self):
        User.objects.create_user(
            email='newowner@example.com', phone='233433333333',
            password='x', role=User.Role.OWNER)
        assert _admin_notifs(category='NEW_OWNER').exists()

    def test_new_customer_registration_notifies_admins(self):
        User.objects.create_user(
            email='newcust@example.com', phone='233444444444', password='x')
        assert _admin_notifs(category='NEW_USER').exists()

    def test_pending_laundry_notifies_admins(self):
        owner = User.objects.create_user(
            email='ow2@example.com', phone='233455555555', password='x', role=User.Role.OWNER)
        Laundry.objects.create(
            name='Pending Shop', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000001', status=Laundry.ApprovalStatus.PENDING)
        assert _admin_notifs(category='LAUNDRY_PENDING').exists()

    def test_new_order_notifies_admins(self, customer):
        owner = User.objects.create_user(
            email='ow3@example.com', phone='233466666666', password='x', role=User.Role.OWNER)
        laundry = Laundry.objects.create(
            name='Order Shop', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000002')
        Order.objects.create(
            user=customer, laundry=laundry, status='PENDING',
            total_amount=10, pickup_date=timezone.now())
        assert _admin_notifs(category='NEW_BOOKING').exists()

    def test_payment_success_notifies_admin_and_customer(self, customer):
        owner = User.objects.create_user(
            email='ow4@example.com', phone='233477777777', password='x', role=User.Role.OWNER)
        laundry = Laundry.objects.create(
            name='Pay Shop', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000003')
        order = Order.objects.create(
            user=customer, laundry=laundry, status='PENDING',
            total_amount=10, pickup_date=timezone.now())
        Payment.objects.create(
            user=customer, order=order, amount=10, currency='GHS',
            transaction_reference='PAYOK1', status=Payment.Status.SUCCESS)
        assert _admin_notifs(category='PAYMENT_SUCCESS').exists()
        assert Notification.objects.filter(
            audience=Notification.Audience.USER, user=customer,
            category='PAYMENT_SUCCESS').exists()

    def test_new_review_notifies_admins(self, customer):
        owner = User.objects.create_user(
            email='ow5@example.com', phone='233488888888', password='x', role=User.Role.OWNER)
        laundry = Laundry.objects.create(
            name='Review Shop', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000004')
        from laundries.models.review import Review
        Review.objects.create(laundry=laundry, user=customer, rating=5, comment='Great')
        assert _admin_notifs(category='NEW_REVIEW').exists()
