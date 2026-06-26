# pyre-ignore[missing-module]
from decimal import Decimal
# pyre-ignore[missing-module]
from django.urls import reverse
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from rest_framework.test import APITestCase
# pyre-ignore[missing-module]
from rest_framework import status

from ordering.models import Order
from payments.models import Payment
from laundries.models.laundry import Laundry
from analytics.services import AnalyticsService

User = get_user_model()


class DashboardBase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="dadmin@example.com", phone="233700000200", password="pw",
            role='ADMIN', is_staff=True)
        self.owner = User.objects.create_user(
            email="downer@example.com", phone="233700000201", password="pw", role='OWNER')
        self.customer = User.objects.create_user(
            email="dcust@example.com", phone="233700000202", password="pw", role='CUSTOMER')
        self.laundry = Laundry.objects.create(
            name="DL", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123409000")
        self.client.force_authenticate(user=self.admin)


class ExecutiveDashboardTests(DashboardBase):
    def test_admin_only(self):
        self.client.force_authenticate(user=self.customer)
        r = self.client.get(reverse('dashboard-executive'))
        self.assertIn(r.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED))

    def test_executive_cards(self):
        Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=50,
                             pickup_date=timezone.now(), address="A")
        r = self.client.get(reverse('dashboard-executive'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        data = r.data['data']
        for key in ('active_users_now', 'orders_today', 'revenue_today',
                    'revenue_this_month', 'new_users_today', 'pending_orders',
                    'notifications_sent_today', 'avg_rating'):
            self.assertIn(key, data)
        self.assertGreaterEqual(data['orders_today'], 1)


class OrderDashboardTests(DashboardBase):
    def test_order_metrics_and_funnel(self):
        Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=100,
                             pickup_date=timezone.now(), address="A", status='DELIVERED',
                             payment_status='PAID')
        Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=50,
                             pickup_date=timezone.now(), address="A", status='CANCELLED')
        r = self.client.get(reverse('dashboard-orders'))
        data = r.data['data']
        self.assertEqual(data['created'], 2)
        self.assertEqual(data['completed'], 1)
        self.assertEqual(data['cancelled'], 1)
        self.assertEqual(data['completion_rate'], 50.0)
        self.assertEqual(len(data['funnel']), 3)


class RevenueDashboardTests(DashboardBase):
    def test_revenue_from_successful_payment(self):
        order = Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=200,
                                     pickup_date=timezone.now(), address="A")
        Payment.objects.create(user=self.customer, order=order, amount=Decimal('200.00'),
                               currency='GHS', status='SUCCESS',
                               transaction_reference='txn-1', paid_at=timezone.now())
        r = self.client.get(reverse('dashboard-revenue'))
        data = r.data['data']
        self.assertEqual(data['gross_revenue'], '200.00')
        self.assertEqual(data['successful_payments'], 1)
        # 5% default platform fee
        self.assertEqual(data['platform_revenue'], '10.00')

    def test_payment_success_rate(self):
        o1 = Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=10,
                                  pickup_date=timezone.now(), address="A")
        o2 = Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=10,
                                  pickup_date=timezone.now(), address="A")
        Payment.objects.create(user=self.customer, order=o1, amount=Decimal('10'), currency='GHS',
                               status='SUCCESS', transaction_reference='t1', paid_at=timezone.now())
        Payment.objects.create(user=self.customer, order=o2, amount=Decimal('10'), currency='GHS',
                               status='FAILED', transaction_reference='t2')
        r = self.client.get(reverse('dashboard-revenue'))
        self.assertEqual(r.data['data']['payment_success_rate'], 50.0)


class UsersDashboardTests(DashboardBase):
    def test_users_dashboard(self):
        AnalyticsService.record('APP_OPEN', user=self.customer, session_id='s1', platform='ios')
        r = self.client.get(reverse('dashboard-users'))
        data = r.data['data']
        self.assertIn('dau', data)
        self.assertIn('wau', data)
        self.assertIn('mau', data)
        self.assertGreaterEqual(data['total_customers'], 1)


class ReferralDashboardTests(DashboardBase):
    def test_referral_metrics(self):
        User.objects.create_user(email="rr@example.com", phone="233700000203", password="pw",
                                 role='CUSTOMER', referred_by=self.customer)
        r = self.client.get(reverse('dashboard-referrals'))
        data = r.data['data']
        self.assertEqual(data['total_referred_users'], 1)
        self.assertEqual(data['referrers'], 1)
        self.assertTrue(any(t['email'] == self.customer.email for t in data['top_referrers']))


class ExportTests(DashboardBase):
    def test_csv_export_orders(self):
        Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=15,
                             pickup_date=timezone.now(), address="A")
        r = self.client.get(reverse('dashboard-export'), {'dataset': 'orders'})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn('attachment', r['Content-Disposition'])
        body = r.content.decode()
        self.assertIn('order_no', body)

    def test_csv_export_admin_only(self):
        self.client.force_authenticate(user=self.customer)
        r = self.client.get(reverse('dashboard-export'), {'dataset': 'orders'})
        self.assertIn(r.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED))

    def test_xlsx_export(self):
        Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=15,
                             pickup_date=timezone.now(), address="A")
        r = self.client.get(reverse('dashboard-export'), {'dataset': 'orders', 'fmt': 'xlsx'})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(
            r['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('.xlsx', r['Content-Disposition'])
        # XLSX is a zip — starts with PK signature.
        self.assertEqual(r.content[:2], b'PK')

    def test_pdf_export(self):
        Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=15,
                             pickup_date=timezone.now(), address="A")
        r = self.client.get(reverse('dashboard-export'), {'dataset': 'payments', 'fmt': 'pdf'})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(r.content[:4], b'%PDF')


class AdminPageTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="pageadmin@example.com", phone="233700000210", password="pw",
            role='ADMIN', is_staff=True, is_superuser=True)
        self.customer = User.objects.create_user(
            email="pagecust@example.com", phone="233700000211", password="pw", role='CUSTOMER')

    def test_dashboard_page_renders_for_staff(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin-analytics-dashboard'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn(b'Analytics', r.content)
        self.assertIn(b'dauChart', r.content)

    def test_dashboard_page_blocks_non_staff(self):
        self.client.force_login(self.customer)
        r = self.client.get(reverse('admin-analytics-dashboard'))
        # staff_member_required redirects non-staff to the admin login.
        self.assertIn(r.status_code, (302, 403))

    def test_admin_export_xlsx(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin-analytics-export'),
                            {'dataset': 'events', 'fmt': 'xlsx'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:2], b'PK')
