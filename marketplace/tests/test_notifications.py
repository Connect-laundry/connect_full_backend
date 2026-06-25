# pyre-ignore[missing-module]
from django.urls import reverse
# pyre-ignore[missing-module]
from rest_framework.test import APITestCase
# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model
# pyre-ignore[missing-module]
from marketplace.models import (
    Notification, NotificationPreference, NotificationCampaign, PushDevice,
)
# pyre-ignore[missing-module]
from marketplace.services.notification_service import NotificationService
# pyre-ignore[missing-module]
from marketplace.services.campaign_service import CampaignService
# pyre-ignore[missing-module]
from ordering.models import Order
# pyre-ignore[missing-module]
from ordering.services.order_state_machine import order_status_changed
# pyre-ignore[missing-module]
from laundries.models.laundry import Laundry
# pyre-ignore[missing-module]
from django.utils import timezone
from django.test import override_settings
from unittest.mock import patch
from datetime import timedelta

User = get_user_model()

class NotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", phone="233333333333", password="password", role='CUSTOMER')
        self.owner = User.objects.create_user(email="owner@example.com", phone="233444444444", password="password", role='OWNER')
        self.laundry = Laundry.objects.create(name="Test Laundry", owner=self.owner, address="Test Address", latitude=5.6, longitude=-0.1, phone_number="0123456789")
        self.client.force_authenticate(user=self.user)

    @patch('marketplace.tasks.create_notification.delay')
    def test_order_creation_triggers_owner_notification(self, mock_task):
        """Creating an order should trigger a notification to the laundry owner."""
        Order.objects.create(
            user=self.user,
            laundry=self.laundry,
            total_amount=100.00,
            pickup_date=timezone.now(),
            address="Test Address"
        )
        mock_task.assert_called()

    def test_mark_as_read(self):
        notification = Notification.objects.create(
            user=self.user,
            title="Test",
            body="Test body"
        )
        url = reverse('notification-mark-read', kwargs={'pk': notification.id})
        response = self.client.patch(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_read(self):
        Notification.objects.create(user=self.user, title="1", body="1")
        Notification.objects.create(user=self.user, title="2", body="2")
        url = reverse('notification-mark-all-read')
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 0)


@override_settings(EXPO_PUSH_ENABLED=True)
class PreferenceEnforcementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="pref@example.com", phone="233700000001", password="pw", role='CUSTOMER')

    @patch('marketplace.tasks.send_real_push.delay')
    def test_push_sent_when_category_enabled(self, mock_push):
        NotificationService.notify_user(
            self.user, title="t", body="b", category='ORDER',
            type=Notification.Type.ORDER)
        mock_push.assert_called_once()

    @patch('marketplace.tasks.send_real_push.delay')
    def test_push_blocked_when_category_disabled(self, mock_push):
        pref = NotificationService.get_preferences(self.user)
        pref.order_updates = False
        pref.save()
        n = NotificationService.notify_user(
            self.user, title="t", body="b", category='ORDER',
            type=Notification.Type.ORDER)
        # In-app record still created (history), but no push queued.
        self.assertIsNotNone(n)
        mock_push.assert_not_called()

    @patch('marketplace.tasks.send_real_push.delay')
    def test_push_blocked_when_master_off(self, mock_push):
        pref = NotificationService.get_preferences(self.user)
        pref.push_enabled = False
        pref.save()
        NotificationService.notify_user(self.user, title="t", body="b", category='PROMO',
                                        type=Notification.Type.PROMO)
        mock_push.assert_not_called()

    @patch('marketplace.tasks.send_real_push.delay')
    def test_quiet_hours_blocks_normal_but_not_urgent(self, mock_push):
        pref = NotificationService.get_preferences(self.user)
        current_hour = timezone.localtime(timezone.now()).hour
        # A 3-hour quiet window covering "now".
        pref.quiet_hours_start = current_hour
        pref.quiet_hours_end = (current_hour + 3) % 24
        pref.save()

        NotificationService.notify_user(self.user, title="n", body="b", category='ORDER',
                                        type=Notification.Type.ORDER)
        mock_push.assert_not_called()

        NotificationService.notify_user(
            self.user, title="u", body="b", category='ORDER',
            type=Notification.Type.ORDER, priority=Notification.Priority.URGENT)
        mock_push.assert_called_once()


@override_settings(EXPO_PUSH_ENABLED=True)
class NoDuplicateOrderNotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="dup@example.com", phone="233700000002", password="pw", role='CUSTOMER')
        self.owner = User.objects.create_user(
            email="dupowner@example.com", phone="233700000003", password="pw", role='OWNER')
        self.laundry = Laundry.objects.create(
            name="L", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123456789")
        self.order = Order.objects.create(
            user=self.user, laundry=self.laundry, total_amount=10,
            pickup_date=timezone.now(), address="A")

    @patch('marketplace.tasks.send_real_push.delay')
    def test_status_change_creates_single_customer_notification(self, _push):
        # Fire the same transition twice — dedup must collapse to one record.
        for _ in range(2):
            order_status_changed.send(
                sender=Order, order=self.order,
                from_status='PENDING', to_status='CONFIRMED', user=self.user,
                metadata={})

        confirmed = Notification.objects.filter(
            user=self.user, audience=Notification.Audience.USER,
            dedup_key=f'order_status:{self.order.id}:CONFIRMED')
        self.assertEqual(confirmed.count(), 1)


class TokenCleanupTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="tok@example.com", phone="233700000004", password="pw", role='CUSTOMER')
        self.device = PushDevice.objects.create(
            user=self.user, token="ExponentPushToken[GOODBAD]", platform='android')

    @patch('marketplace.tasks.requests.post')
    def test_device_not_registered_deactivates_token(self, mock_post):
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            "data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]
        }
        from marketplace.tasks import deliver_push
        deliver_push("t", "b", {}, ["ExponentPushToken[GOODBAD]"])
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)


@override_settings(EXPO_PUSH_ENABLED=True)
class CampaignTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="cowner@example.com", phone="233700000010", password="pw", role='OWNER')
        self.laundry = Laundry.objects.create(
            name="CL", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123456780")
        self.pending_user = User.objects.create_user(
            email="pending@example.com", phone="233700000011", password="pw", role='CUSTOMER')
        self.idle_user = User.objects.create_user(
            email="idle@example.com", phone="233700000012", password="pw", role='CUSTOMER')
        Order.objects.create(
            user=self.pending_user, laundry=self.laundry, total_amount=10,
            pickup_date=timezone.now(), address="A", status='PENDING')

    def test_pending_orders_segment_resolves_correct_users(self):
        recipients = list(CampaignService.resolve_recipients(
            NotificationCampaign.Segment.PENDING_ORDERS))
        self.assertIn(self.pending_user, recipients)
        self.assertNotIn(self.idle_user, recipients)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_campaign_skips_opted_out_user(self, _push):
        pref = NotificationService.get_preferences(self.pending_user)
        pref.campaigns = False
        pref.save()

        delivered, skipped = CampaignService.deliver(
            recipients=[self.pending_user, self.idle_user],
            title="Come back", body="We miss you", category='CAMPAIGN',
            dedup_prefix='test_campaign')

        self.assertEqual(delivered, 1)   # idle_user only
        self.assertEqual(skipped, 1)     # pending_user opted out
        self.assertFalse(Notification.objects.filter(
            user=self.pending_user, dedup_key__startswith='test_campaign').exists())

    @patch('marketplace.tasks.send_real_push.delay')
    def test_campaign_frequency_cap_dedup(self, _push):
        # Same period_key twice → second send deduped to no new record.
        for _ in range(2):
            CampaignService.deliver(
                recipients=[self.idle_user], title="Hi", body="b",
                category='CAMPAIGN', dedup_prefix='freq', period_key='2026W26')
        self.assertEqual(
            Notification.objects.filter(user=self.idle_user, dedup_key='freq:%s:2026W26' % self.idle_user.id).count(),
            1)

    @override_settings(
        WEATHER_PROMO_ENABLED=True,
        WEATHER_PROMO_PROVIDER='open-meteo',
        WEATHER_PROMO_LATITUDE='5.6037',
        WEATHER_PROMO_LONGITUDE='-0.1870',
        WEATHER_PROMO_LOOKAHEAD_HOURS=6,
        WEATHER_PROMO_RAIN_PROBABILITY_THRESHOLD=60,
        WEATHER_PROMO_MIN_RAIN_MM=0.1,
        WEATHER_PROMO_OPEN_METEO_URL='https://weather.example.test/forecast',
        WEATHER_PROMO_TITLE='Rainy day laundry rescue',
        WEATHER_PROMO_BODY='Rain is likely today. Schedule a pickup.',
        WEATHER_PROMO_ACTION_URL='/home',
    )
    @patch('marketplace.tasks.run_campaign.delay')
    @patch('marketplace.services.weather_campaign.requests.get')
    def test_rainy_day_campaign_queues_existing_engine(self, mock_get, mock_run):
        from marketplace.services.weather_campaign import WeatherCampaignService

        forecast_time = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json = lambda: {
            'hourly': {
                'time': [forecast_time.strftime('%Y-%m-%dT%H:00')],
                'precipitation_probability': [75],
                'rain': [0.2],
                'showers': [0],
                'weather_code': [61],
            }
        }

        campaign = WeatherCampaignService.enqueue_rainy_day_campaign()

        self.assertIsNotNone(campaign)
        self.assertEqual(campaign.segment, NotificationCampaign.Segment.PROMO_OPT_IN)
        self.assertEqual(campaign.category, 'PROMO')
        mock_run.assert_called_once_with(str(campaign.id))

    @override_settings(
        WEATHER_PROMO_ENABLED=True,
        WEATHER_PROMO_PROVIDER='open-meteo',
        WEATHER_PROMO_LATITUDE='5.6037',
        WEATHER_PROMO_LONGITUDE='-0.1870',
        WEATHER_PROMO_LOOKAHEAD_HOURS=6,
        WEATHER_PROMO_RAIN_PROBABILITY_THRESHOLD=60,
        WEATHER_PROMO_MIN_RAIN_MM=0.1,
        WEATHER_PROMO_OPEN_METEO_URL='https://weather.example.test/forecast',
    )
    @patch('marketplace.services.weather_campaign.requests.get')
    def test_rainy_day_campaign_skips_clear_forecast(self, mock_get):
        from marketplace.services.weather_campaign import WeatherCampaignService

        forecast_time = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json = lambda: {
            'hourly': {
                'time': [forecast_time.strftime('%Y-%m-%dT%H:00')],
                'precipitation_probability': [10],
                'rain': [0],
                'showers': [0],
                'weather_code': [1],
            }
        }

        self.assertIsNone(WeatherCampaignService.enqueue_rainy_day_campaign())


class PreferencesAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="prefapi@example.com", phone="233700000020", password="pw", role='CUSTOMER')
        self.client.force_authenticate(user=self.user)

    def test_get_preferences_creates_defaults(self):
        url = reverse('notification-preferences')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['data']['push_enabled'])

    def test_patch_preferences_updates(self):
        url = reverse('notification-preferences')
        response = self.client.patch(url, {'promotions': False, 'quiet_hours_start': 22, 'quiet_hours_end': 7})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pref = NotificationPreference.objects.get(user=self.user)
        self.assertFalse(pref.promotions)
        self.assertEqual(pref.quiet_hours_start, 22)

    def test_patch_rejects_invalid_quiet_hour(self):
        url = reverse('notification-preferences')
        response = self.client.patch(url, {'quiet_hours_start': 30})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
