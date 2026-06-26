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

from ordering.models import Order
from payments.models import Payment
from laundries.models.laundry import Laundry
from laundries.models.review import Review
from analytics import metrics
from analytics.services import AnalyticsService
from config.insights import SECTIONS

User = get_user_model()


class NewMetricsTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="lo@example.com", phone="233700000300", password="pw", role='OWNER')
        self.laundry = Laundry.objects.create(
            name="MetricLaundry", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123450000", is_active=True)
        self.customer = User.objects.create_user(
            email="lc@example.com", phone="233700000301", password="pw", role='CUSTOMER')

    def test_laundry_metrics_top_by_orders_and_rating(self):
        Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=100,
                             pickup_date=timezone.now(), address="A", payment_status='PAID')
        Review.objects.create(laundry=self.laundry, user=self.customer, rating=5)
        m = metrics.laundry_metrics(30)
        self.assertEqual(m["active_laundries"], 1)
        self.assertTrue(any(r["name"] == "MetricLaundry" for r in m["top_by_orders"]))
        self.assertTrue(any(r["rating"] == 5.0 for r in m["top_by_rating"]))

    def test_retention_metrics_shape(self):
        AnalyticsService.record('APP_OPEN', user=self.customer, session_id='s1', platform='ios')
        m = metrics.retention_metrics(30)
        for key in ('day_1_retention', 'day_7_retention', 'day_30_retention',
                    'stickiness', 'returning_rate', 'active_users'):
            self.assertIn(key, m)

    def test_ai_insights_returns_cards_and_forecast(self):
        order = Order.objects.create(user=self.customer, laundry=self.laundry, total_amount=200,
                                     pickup_date=timezone.now(), address="A")
        Payment.objects.create(user=self.customer, order=order, amount=Decimal('200'),
                               currency='GHS', status='SUCCESS', transaction_reference='ai-1',
                               paid_at=timezone.now())
        ai = metrics.ai_insights(7)
        self.assertIn('insights', ai)
        self.assertIn('revenue_forecast_30d', ai)
        self.assertIsInstance(ai['insights'], list)

    def test_realtime_feed_shape(self):
        AnalyticsService.record('APP_OPEN', user=self.customer, session_id='s9', platform='android')
        rt = metrics.realtime_feed()
        self.assertGreaterEqual(rt['active_sessions'], 1)
        self.assertIn('recent_events', rt)
        self.assertIn('recent_orders', rt)


class InsightsPageTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="iadmin@example.com", phone="233700000310", password="pw",
            role='ADMIN', is_staff=True, is_superuser=True)
        self.customer = User.objects.create_user(
            email="icust@example.com", phone="233700000311", password="pw", role='CUSTOMER')

    def test_all_sections_render_for_staff(self):
        self.client.force_login(self.admin)
        for key, label, _icon in SECTIONS:
            r = self.client.get(reverse('insights-section', kwargs={'section': key}))
            self.assertEqual(r.status_code, 200, f"section {key} failed: {r.status_code}")
            self.assertIn(b'Connect Insights', r.content)

    def test_home_defaults_to_overview(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('insights-home'))
        self.assertEqual(r.status_code, 200)

    def test_unknown_section_404(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('insights-section', kwargs={'section': 'bogus'}))
        self.assertEqual(r.status_code, 404)

    def test_non_staff_blocked(self):
        self.client.force_login(self.customer)
        r = self.client.get(reverse('insights-section', kwargs={'section': 'overview'}))
        self.assertIn(r.status_code, (302, 403))
