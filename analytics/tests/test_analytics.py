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

from analytics.models import AnalyticsEvent
from analytics.services import AnalyticsService, redact_event_data

User = get_user_model()


class RedactionTests(APITestCase):
    def test_drops_sensitive_keys(self):
        out = redact_event_data({'password': 'x', 'token': 'y', 'screen': 'home'})
        self.assertEqual(out['password'], '[redacted]')
        self.assertEqual(out['token'], '[redacted]')
        self.assertEqual(out['screen'], 'home')

    def test_truncates_long_values(self):
        out = redact_event_data({'note': 'a' * 1000})
        self.assertLessEqual(len(out['note']), 500)

    def test_non_dict_returns_empty(self):
        self.assertEqual(redact_event_data('nope'), {})
        self.assertEqual(redact_event_data(None), {})

    def test_caps_key_count(self):
        big = {f'k{i}': i for i in range(200)}
        self.assertLessEqual(len(redact_event_data(big)), 50)


class IngestTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="a@example.com", phone="233700000100", password="pw", role='CUSTOMER')

    def test_requires_auth(self):
        r = self.client.post(reverse('analytics-ingest'), {'events': []}, format='json')
        self.assertIn(r.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_batch_ingest_persists_and_redacts(self):
        self.client.force_authenticate(user=self.user)
        payload = {'events': [
            {'event_name': 'APP_OPEN', 'platform': 'ios', 'session_id': 's1',
             'app_version': '1.0.0', 'event_data': {'password': 'secret', 'foo': 'bar'}},
            {'event_name': 'SCREEN_VIEW', 'platform': 'ios', 'screen_name': 'home'},
        ]}
        r = self.client.post(reverse('analytics-ingest'), payload, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['data']['accepted'], 2)

        ev = AnalyticsEvent.objects.get(event_name='APP_OPEN')
        self.assertEqual(ev.user, self.user)
        self.assertEqual(ev.event_data['password'], '[redacted]')
        self.assertEqual(ev.event_data['foo'], 'bar')

    def test_empty_batch_rejected(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(reverse('analytics-ingest'), {'events': []}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_oversized_batch_rejected(self):
        self.client.force_authenticate(user=self.user)
        events = [{'event_name': 'X'} for _ in range(101)]
        r = self.client.post(reverse('analytics-ingest'), {'events': events}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class SummaryTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="adm@example.com", phone="233700000101", password="pw",
            role='ADMIN', is_staff=True)
        self.customer = User.objects.create_user(
            email="cust@example.com", phone="233700000102", password="pw", role='CUSTOMER')
        AnalyticsService.record('APP_OPEN', user=self.customer, session_id='s1', platform='ios')
        AnalyticsService.record('SCREEN_VIEW', user=self.customer, session_id='s1', platform='ios')

    def test_summary_admin_only(self):
        self.client.force_authenticate(user=self.customer)
        r = self.client.get(reverse('analytics-summary'))
        self.assertIn(r.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED))

    def test_summary_returns_metrics(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get(reverse('analytics-summary'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        data = r.data['data']
        self.assertEqual(data['total_events'], 2)
        self.assertEqual(data['unique_users'], 1)
        self.assertEqual(data['active_sessions'], 1)
        names = {row['event_name'] for row in data['top_events']}
        self.assertIn('APP_OPEN', names)


class ServerSignalTests(APITestCase):
    def setUp(self):
        from laundries.models.laundry import Laundry
        self.user = User.objects.create_user(
            email="o@example.com", phone="233700000103", password="pw", role='CUSTOMER')
        self.owner = User.objects.create_user(
            email="ow@example.com", phone="233700000104", password="pw", role='OWNER')
        self.laundry = Laundry.objects.create(
            name="L", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123456700")

    def test_order_creation_emits_event(self):
        from ordering.models import Order
        Order.objects.create(user=self.user, laundry=self.laundry, total_amount=25,
                             pickup_date=timezone.now(), address="A")
        self.assertTrue(
            AnalyticsEvent.objects.filter(event_name='ORDER_CREATED', user=self.user).exists())


class PruneOldEventsTests(APITestCase):
    def test_prune_old_events(self):
        from analytics.tasks import prune_old_events
        from datetime import timedelta
        
        # Create a recent event
        e_recent = AnalyticsEvent.objects.create(event_name='APP_OPEN')
        
        # Create an old event (we will update its created_at to bypass auto_now_add)
        e_old = AnalyticsEvent.objects.create(event_name='APP_OPEN')
        cutoff_date = timezone.now() - timedelta(days=181)
        AnalyticsEvent.objects.filter(id=e_old.id).update(created_at=cutoff_date)
        
        # Run prune task with default retention (180 days)
        prune_old_events()
        
        # Assert e_recent still exists, but e_old is deleted
        self.assertTrue(AnalyticsEvent.objects.filter(id=e_recent.id).exists())
        self.assertFalse(AnalyticsEvent.objects.filter(id=e_old.id).exists())

    def test_prune_old_events_custom_retention(self):
        from analytics.tasks import prune_old_events
        from django.test import override_settings
        from datetime import timedelta
        
        e_recent = AnalyticsEvent.objects.create(event_name='APP_OPEN')
        e_old = AnalyticsEvent.objects.create(event_name='APP_OPEN')
        # make it 35 days old
        cutoff_date = timezone.now() - timedelta(days=35)
        AnalyticsEvent.objects.filter(id=e_old.id).update(created_at=cutoff_date)
        
        # Run prune task with custom retention of 30 days
        with override_settings(ANALYTICS_RETENTION_DAYS=30):
            prune_old_events()
            
        # Assert e_recent still exists, but e_old is deleted
        self.assertTrue(AnalyticsEvent.objects.filter(id=e_recent.id).exists())
        self.assertFalse(AnalyticsEvent.objects.filter(id=e_old.id).exists())

