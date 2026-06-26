"""Tests for Sentry Errors panel, city/laundry filters, and email reports."""
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.test import override_settings
from rest_framework.test import APITestCase

from ordering.models import Order
from payments.models import Payment
from laundries.models.laundry import Laundry
from users.models import Address
from analytics import metrics, sentry_service

User = get_user_model()


class SentryServiceTests(APITestCase):
    @override_settings(SENTRY_API_TOKEN='', SENTRY_ORG_SLUG='', SENTRY_PROJECT_SLUG='')
    def test_not_configured(self):
        self.assertFalse(sentry_service.is_configured())
        out = sentry_service.get_issues(use_cache=False)
        self.assertFalse(out['configured'])
        self.assertEqual(out['issues'], [])

    @override_settings(SENTRY_API_TOKEN='tok', SENTRY_ORG_SLUG='org',
                       SENTRY_PROJECT_SLUG='proj', SENTRY_API_BASE='https://sentry.io/api/0')
    @patch('analytics.sentry_service.requests.get')
    def test_get_issues_parses_payload(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=lambda: None,
            json=lambda: [{
                'title': 'KeyError: x', 'culprit': 'views.py', 'level': 'error',
                'count': '12', 'userCount': 3, 'permalink': 'http://s/1',
                'lastSeen': '2026-01-01T00:00:00Z',
            }],
        )
        out = sentry_service.get_issues(use_cache=False)
        self.assertTrue(out['configured'])
        self.assertEqual(len(out['issues']), 1)
        self.assertEqual(out['issues'][0]['title'], 'KeyError: x')

    @override_settings(SENTRY_API_TOKEN='tok', SENTRY_ORG_SLUG='org', SENTRY_PROJECT_SLUG='proj')
    @patch('analytics.sentry_service.requests.get', side_effect=Exception('boom'))
    def test_network_failure_degrades(self, _mock):
        out = sentry_service.get_issues(use_cache=False)
        self.assertTrue(out['configured'])
        self.assertEqual(out['issues'], [])
        self.assertIsNotNone(out['error'])


class FilterTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="fo@example.com", phone="233700000400", password="pw", role='OWNER')
        self.accra_l = Laundry.objects.create(
            name="AccraWash", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123400100", city='Accra', is_active=True)
        self.kumasi_l = Laundry.objects.create(
            name="KumasiWash", owner=self.owner, address="K", latitude=6.7, longitude=-1.6,
            phone_number="0123400101", city='Kumasi', is_active=True)
        self.cust = User.objects.create_user(
            email="fc@example.com", phone="233700000401", password="pw", role='CUSTOMER')
        Order.objects.create(user=self.cust, laundry=self.accra_l, total_amount=100,
                             pickup_date=timezone.now(), address="A")
        Order.objects.create(user=self.cust, laundry=self.kumasi_l, total_amount=50,
                             pickup_date=timezone.now(), address="K")

    def test_order_metrics_city_filter(self):
        self.assertEqual(metrics.order_metrics(30)['created'], 2)
        self.assertEqual(metrics.order_metrics(30, city='Accra')['created'], 1)

    def test_order_metrics_laundry_filter(self):
        self.assertEqual(metrics.order_metrics(30, laundry_id=str(self.kumasi_l.id))['created'], 1)

    def test_revenue_metrics_city_filter(self):
        for laundry in (self.accra_l, self.kumasi_l):
            o = Order.objects.create(user=self.cust, laundry=laundry, total_amount=100,
                                     pickup_date=timezone.now(), address="A")
            Payment.objects.create(user=self.cust, order=o, amount=Decimal('100'), currency='GHS',
                                   status='SUCCESS', transaction_reference=f't-{o.id}',
                                   paid_at=timezone.now())
        self.assertEqual(metrics.revenue_metrics(30, city='Accra')['gross_revenue'], '100.00')


class FilteredPageTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="fadmin@example.com", phone="233700000410", password="pw",
            role='ADMIN', is_staff=True, is_superuser=True)

    def test_orders_section_accepts_filters(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('insights-section', kwargs={'section': 'orders'}),
                            {'city': 'Accra', 'days': 30})
        self.assertEqual(r.status_code, 200)
        # filter selectors present on a filterable section
        self.assertIn(b'All cities', r.content)


class EmailReportTests(APITestCase):
    @override_settings(ANALYTICS_REPORT_RECIPIENTS=[])
    def test_noop_without_recipients(self):
        from analytics.reports import email_period_report
        self.assertEqual(email_period_report('daily'), 0)

    @override_settings(ANALYTICS_REPORT_RECIPIENTS=['ops@example.com'])
    @patch('analytics.reports.EmailMessage')
    def test_sends_with_attachments(self, mock_email_cls):
        from analytics.reports import email_period_report
        instance = MagicMock()
        instance.send.return_value = 1
        mock_email_cls.return_value = instance

        result = email_period_report('weekly')

        self.assertEqual(result, 1)
        instance.send.assert_called_once()
        # PDF + CSV attached.
        self.assertEqual(instance.attach.call_count, 2)
