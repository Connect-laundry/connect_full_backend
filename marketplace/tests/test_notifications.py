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
    Notification, NotificationEventClaim, NotificationPreference,
    NotificationCampaign, PushDevice, PushDelivery,
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
from django.conf import settings
from users.models import Address, DeviceSession
from unittest.mock import patch
from datetime import timedelta

User = get_user_model()

class NotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", phone="233333333333", password="password", role='CUSTOMER')
        self.owner = User.objects.create_user(email="owner@example.com", phone="233444444444", password="password", role='OWNER')
        self.laundry = Laundry.objects.create(name="Test Laundry", owner=self.owner, address="Test Address", latitude=5.6, longitude=-0.1, phone_number="0123456789")
        self.client.force_authenticate(user=self.user)

    def test_order_creation_triggers_one_owner_notification(self):
        """Creating an order should trigger a notification to the laundry owner."""
        Order.objects.create(
            user=self.user,
            laundry=self.laundry,
            total_amount=100.00,
            pickup_date=timezone.now(),
            address="Test Address"
        )
        self.assertEqual(
            Notification.objects.filter(
                user=self.owner, category='ORDER_CREATED',
            ).count(),
            1,
        )

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

    def test_notification_feed_is_bounded_and_newest_first(self):
        Notification.objects.filter(user=self.user).delete()
        created = [
            Notification.objects.create(
                user=self.user, title=f'notification-{index}', body='body',
            )
            for index in range(120)
        ]
        response = self.client.get(reverse('notification-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 120)
        self.assertEqual(len(response.data['results']), 50)
        self.assertEqual(response.data['results'][0]['id'], str(created[-1].id))
        self.assertIsNotNone(response.data['next'])


@override_settings(EXPO_PUSH_ENABLED=True)
class PreferenceEnforcementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="pref@example.com", phone="233700000001", password="pw", role='CUSTOMER')

    @patch('marketplace.tasks.send_real_push.delay')
    def test_push_sent_when_category_enabled(self, mock_push):
        # Pushes are dispatched on transaction commit so they never fire from
        # inside a transaction that might roll back.
        with self.captureOnCommitCallbacks(execute=True):
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

        with self.captureOnCommitCallbacks(execute=True):
            NotificationService.notify_user(self.user, title="n", body="b", category='ORDER',
                                            type=Notification.Type.ORDER)
        mock_push.assert_not_called()

        with self.captureOnCommitCallbacks(execute=True):
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
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            "data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]
        }
        from marketplace.tasks import deliver_push
        deliver_push("t", "b", {}, ["ExponentPushToken[GOODBAD]"])
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)


class ExpoTransportTests(APITestCase):
    """The Expo wire format: auth header, batching, and error surfacing."""

    @override_settings(EXPO_ACCESS_TOKEN='secret-token')
    def test_access_token_is_sent_as_bearer(self):
        from marketplace.tasks import expo_push_headers
        headers = expo_push_headers()
        self.assertEqual(headers['Authorization'], 'Bearer secret-token')

    @override_settings(EXPO_ACCESS_TOKEN='')
    def test_no_authorization_header_when_token_unset(self):
        from marketplace.tasks import expo_push_headers
        self.assertNotIn('Authorization', expo_push_headers())

    @override_settings(EXPO_ACCESS_TOKEN='secret-token')
    @patch('marketplace.tasks.requests.post')
    def test_deliver_push_authenticates_the_send(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"data": [{"status": "ok", "id": "t-1"}]}

        from marketplace.tasks import deliver_push
        sent = deliver_push("t", "b", {}, ["ExponentPushToken[AAAA]"])

        self.assertEqual(sent, 1)
        self.assertEqual(
            mock_post.call_args.kwargs['headers']['Authorization'], 'Bearer secret-token',
        )

    @patch('marketplace.tasks.requests.post')
    def test_deliver_push_splits_batches_of_100(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            "data": [{"status": "ok", "id": "t"}] * 100
        }

        from marketplace.tasks import deliver_push
        tokens = [f"ExponentPushToken[T{i:04d}]" for i in range(150)]
        deliver_push("t", "b", {}, tokens)

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(len(mock_post.call_args_list[0].kwargs['json']), 100)
        self.assertEqual(len(mock_post.call_args_list[1].kwargs['json']), 50)

    @patch('marketplace.tasks.requests.post')
    def test_fixture_loads_through_1000_stay_within_expo_limits(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            'data': [{'status': 'ok', 'id': 'fixture-ticket'}] * 100,
        }
        from marketplace.tasks import deliver_push

        for token_count, expected_requests in ((10, 1), (100, 1), (500, 5), (1000, 10)):
            with self.subTest(token_count=token_count):
                mock_post.reset_mock()
                tokens = [f'ExponentPushToken[LOAD{i:04d}]' for i in range(token_count)]
                self.assertEqual(deliver_push('t', 'b', {}, tokens), token_count)
                self.assertEqual(mock_post.call_count, expected_requests)
                self.assertEqual(
                    sum(len(call.kwargs['json']) for call in mock_post.call_args_list),
                    token_count,
                )
                self.assertTrue(
                    all(len(call.kwargs['json']) <= 100 for call in mock_post.call_args_list),
                )

    @patch('marketplace.tasks.requests.post')
    def test_ticket_rate_limit_is_retryable(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            'data': [{
                'status': 'error',
                'message': 'Rate exceeded',
                'details': {'error': 'MessageRateExceeded'},
            }],
        }
        from marketplace.tasks import deliver_push
        import requests

        with self.assertRaises(requests.RequestException):
            deliver_push('title', 'body', {}, ['ExpoPushToken[rate-limited]'])

    @patch('marketplace.services.notification_service.NotificationService.system_alert')
    @patch('marketplace.tasks.requests.post')
    def test_ticket_credential_error_creates_admin_alert(self, mock_post, mock_alert):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            'data': [{
                'status': 'error',
                'message': 'Credentials rejected',
                'details': {'error': 'InvalidCredentials'},
            }],
        }
        from marketplace.tasks import deliver_push

        self.assertEqual(
            deliver_push('title', 'body', {}, ['ExpoPushToken[credential-error]']),
            0,
        )
        mock_alert.assert_called_once()

    @patch('marketplace.tasks.requests.post')
    def test_rejected_send_raises_so_celery_retries(self, mock_post):
        import requests as requests_lib

        def _raise():
            raise requests_lib.HTTPError("401 Unauthorized")

        mock_post.return_value.status_code = 401
        mock_post.return_value.text = '{"errors":[{"code":"UNAUTHORIZED"}]}'
        mock_post.return_value.raise_for_status = _raise

        from marketplace.tasks import deliver_push
        with self.assertRaises(requests_lib.HTTPError):
            deliver_push("t", "b", {}, ["ExponentPushToken[AAAA]"])


@override_settings(EXPO_PUSH_ENABLED=True)
class BrokerOutageDeliveryTests(APITestCase):
    """Broker outages must preserve the durable row without blocking HTTP.

    The durable PENDING row is recovered by the periodic dispatcher when the
    broker returns; Expo is never called from a customer request.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="broker@example.com", phone="233700000055", password="pw", role='CUSTOMER')
        PushDevice.objects.create(
            user=self.user, token="ExponentPushToken[BROKER]", platform='android')

    @patch('marketplace.tasks.send_real_push.delay')
    def test_push_stays_pending_when_broker_is_down(self, mock_delay):
        from kombu.exceptions import OperationalError
        mock_delay.side_effect = OperationalError("broker unreachable")

        with self.captureOnCommitCallbacks(execute=True):
            notification = NotificationService.notify_user(
                self.user, title="t", body="b", category='ORDER',
                type=Notification.Type.ORDER,
            )

        self.assertIsNotNone(notification)
        mock_delay.assert_called_once()
        notification.refresh_from_db()
        self.assertEqual(notification.push_status, Notification.PushStatus.PENDING)
        self.assertIsNone(notification.push_last_queued_at)

    @patch('marketplace.tasks.requests.post')
    @patch('marketplace.tasks.send_real_push.delay')
    def test_broker_outage_never_calls_expo_in_request_path(self, mock_delay, mock_post):
        from kombu.exceptions import OperationalError
        mock_delay.side_effect = OperationalError("broker unreachable")
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"data": [{"status": "ok", "id": "t-1"}]}

        with self.captureOnCommitCallbacks(execute=True):
            notification = NotificationService.notify_user(
                self.user, title="Order picked up", body="Your laundry is on its way",
                category='ORDER', type=Notification.Type.ORDER,
            )

        mock_post.assert_not_called()
        notification.refresh_from_db()
        self.assertEqual(notification.push_status, Notification.PushStatus.PENDING)


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



@override_settings(EXPO_PUSH_ENABLED=True)
class AnalyticsTrackingTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="track@example.com", phone="233700000030", password="pw", role='CUSTOMER')
        self.client.force_authenticate(user=self.user)
        self.campaign = NotificationCampaign.objects.create(
            name="C", segment=NotificationCampaign.Segment.ALL, title="t", body="b",
            category='CAMPAIGN')

    @patch('marketplace.tasks.send_real_push.delay')
    def test_notify_user_sets_push_status_pending(self, _push):
        n = NotificationService.notify_user(self.user, title="t", body="b", category='ORDER',
                                            type=Notification.Type.ORDER)
        self.assertEqual(n.push_status, Notification.PushStatus.PENDING)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_notify_user_sets_push_status_skipped_when_opted_out(self, _push):
        pref = NotificationService.get_preferences(self.user)
        pref.order_updates = False
        pref.save()
        n = NotificationService.notify_user(self.user, title="t", body="b", category='ORDER',
                                            type=Notification.Type.ORDER)
        self.assertEqual(n.push_status, Notification.PushStatus.SKIPPED)

    def test_track_opened_and_clicked_rolls_up_to_campaign(self):
        n = Notification.objects.create(user=self.user, title="t", body="b", campaign=self.campaign)
        url = reverse('notification-track', kwargs={'pk': n.id})

        r1 = self.client.post(url, {'event': 'opened'})
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        # Repeat open must be idempotent.
        self.client.post(url, {'event': 'opened'})

        r2 = self.client.post(url, {'event': 'clicked'})
        self.assertEqual(r2.status_code, status.HTTP_200_OK)

        n.refresh_from_db()
        self.campaign.refresh_from_db()
        self.assertIsNotNone(n.opened_at)
        self.assertIsNotNone(n.clicked_at)
        self.assertTrue(n.is_read)
        self.assertEqual(self.campaign.opened_count, 1)
        self.assertEqual(self.campaign.clicked_count, 1)

    def test_campaign_rate_properties(self):
        self.campaign.recipients_count = 100
        self.campaign.delivered_count = 80
        self.campaign.opened_count = 40
        self.campaign.clicked_count = 20
        self.campaign.failed_count = 5
        self.campaign.save()
        self.assertEqual(self.campaign.delivery_rate, 80.0)
        self.assertEqual(self.campaign.open_rate, 50.0)
        self.assertEqual(self.campaign.click_rate, 25.0)
        self.assertEqual(self.campaign.failure_rate, 5.0)


class SegmentTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="sowner@example.com", phone="233700000040", password="pw", role='OWNER')
        self.laundry = Laundry.objects.create(
            name="SL", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123400000")
        self.referrer = User.objects.create_user(
            email="ref@example.com", phone="233700000041", password="pw", role='CUSTOMER')
        self.referred = User.objects.create_user(
            email="referred@example.com", phone="233700000042", password="pw",
            role='CUSTOMER', referred_by=self.referrer)
        self.never = User.objects.create_user(
            email="never@example.com", phone="233700000043", password="pw", role='CUSTOMER')
        self.orderer = User.objects.create_user(
            email="orderer@example.com", phone="233700000044", password="pw", role='CUSTOMER')
        Order.objects.create(user=self.orderer, laundry=self.laundry, total_amount=10,
                             pickup_date=timezone.now(), address="A")

    def test_referral_segment(self):
        recipients = list(CampaignService.resolve_recipients(NotificationCampaign.Segment.REFERRAL))
        self.assertIn(self.referrer, recipients)
        self.assertNotIn(self.referred, recipients)

    def test_never_ordered_segment(self):
        recipients = list(CampaignService.resolve_recipients(NotificationCampaign.Segment.NEVER_ORDERED))
        self.assertIn(self.never, recipients)
        self.assertNotIn(self.orderer, recipients)

    def test_custom_segment_uses_explicit_ids(self):
        recipients = list(CampaignService.resolve_recipients(
            NotificationCampaign.Segment.CUSTOM, {'user_ids': [str(self.never.id)]}))
        self.assertEqual(recipients, [self.never])


@override_settings(EXPO_PUSH_ENABLED=True)
class ScheduledCampaignTests(APITestCase):
    @patch('marketplace.tasks.run_campaign.delay')
    def test_due_scheduled_campaign_is_queued(self, mock_run):
        from marketplace.tasks import process_scheduled_campaigns
        c = NotificationCampaign.objects.create(
            name="due", segment=NotificationCampaign.Segment.ALL, title="t", body="b",
            status=NotificationCampaign.Status.SCHEDULED,
            scheduled_for=timezone.now() - timedelta(minutes=1))
        process_scheduled_campaigns()
        mock_run.assert_called_once_with(str(c.id))

    @patch('marketplace.tasks.run_campaign.delay')
    def test_future_campaign_not_queued(self, mock_run):
        from marketplace.tasks import process_scheduled_campaigns
        NotificationCampaign.objects.create(
            name="future", segment=NotificationCampaign.Segment.ALL, title="t", body="b",
            status=NotificationCampaign.Status.SCHEDULED,
            scheduled_for=timezone.now() + timedelta(hours=2))
        process_scheduled_campaigns()
        mock_run.assert_not_called()

    @patch('marketplace.tasks.run_campaign.delay')
    def test_expired_campaign_marked_failed(self, mock_run):
        from marketplace.tasks import process_scheduled_campaigns
        c = NotificationCampaign.objects.create(
            name="expired", segment=NotificationCampaign.Segment.ALL, title="t", body="b",
            status=NotificationCampaign.Status.SCHEDULED,
            scheduled_for=timezone.now() - timedelta(hours=2),
            expires_at=timezone.now() - timedelta(hours=1))
        process_scheduled_campaigns()
        c.refresh_from_db()
        self.assertEqual(c.status, NotificationCampaign.Status.FAILED)
        mock_run.assert_not_called()


class AdminCampaignAPITests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="adminc@example.com", phone="233700000050", password="pw",
            role='ADMIN', is_staff=True)
        self.customer = User.objects.create_user(
            email="custc@example.com", phone="233700000051", password="pw", role='CUSTOMER')
        self.client.force_authenticate(user=self.admin)

    def test_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.customer)
        r = self.client.get(reverse('campaign-list'))
        self.assertIn(r.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED))

    def test_create_and_preview_and_send(self):
        create = self.client.post(reverse('campaign-list'), {
            'name': 'Launch', 'segment': 'ALL', 'title': 'Hi', 'body': 'There',
        }, format='json')
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)
        campaign_id = create.data['id']

        preview = self.client.post(reverse('campaign-preview'), {'segment': 'ALL'}, format='json')
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(preview.data['data']['audience_size'], 1)

        with patch('marketplace.tasks.run_campaign.delay') as mock_run:
            send = self.client.post(reverse('campaign-send', kwargs={'pk': campaign_id}))
            self.assertEqual(send.status_code, status.HTTP_200_OK)
            mock_run.assert_called_once_with(str(campaign_id))

    def test_schedule_validation(self):
        create = self.client.post(reverse('campaign-list'), {
            'name': 'Later', 'segment': 'ALL', 'title': 'Hi', 'body': 'There',
        }, format='json')
        cid = create.data['id']
        bad = self.client.post(reverse('campaign-schedule', kwargs={'pk': cid}), {})
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        good = self.client.post(reverse('campaign-schedule', kwargs={'pk': cid}),
                                {'scheduled_for': '2030-12-25T08:00:00Z'}, format='json')
        self.assertEqual(good.status_code, status.HTTP_200_OK)

    def test_custom_segment_requires_ids(self):
        r = self.client.post(reverse('campaign-list'), {
            'name': 'C', 'segment': 'CUSTOM', 'title': 'Hi', 'body': 'There',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_analytics_overview(self):
        r = self.client.get(reverse('campaign-analytics-overview'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('delivery_rate', r.data['data'])
        self.assertIn('conversion_rate', r.data['data'])


@override_settings(EXPO_PUSH_ENABLED=True)
class RetentionSystemTests(APITestCase):
    """Phase: customer-retention — city segment, promo codes, conversion, prefs."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="rowner@example.com", phone="233700000060", password="pw", role='OWNER')
        self.laundry = Laundry.objects.create(
            name="RL", owner=self.owner, address="A", latitude=5.6, longitude=-0.1,
            phone_number="0123400001")
        from users.models import Address
        self.accra = User.objects.create_user(
            email="accra@example.com", phone="233700000061", password="pw", role='CUSTOMER')
        self.kumasi = User.objects.create_user(
            email="kumasi@example.com", phone="233700000062", password="pw", role='CUSTOMER')
        Address.objects.create(user=self.accra, label='Home', address_line1='1 St', city='Accra')
        Address.objects.create(user=self.kumasi, label='Home', address_line1='2 St', city='Kumasi')

    def test_city_segment(self):
        recipients = list(CampaignService.resolve_recipients(
            NotificationCampaign.Segment.CITY, {'city': 'Accra'}))
        self.assertIn(self.accra, recipients)
        self.assertNotIn(self.kumasi, recipients)

    def test_city_segment_empty_without_city(self):
        self.assertEqual(
            CampaignService.resolve_recipients(NotificationCampaign.Segment.CITY, {}).count(), 0)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_promo_code_propagates_to_notification(self, _push):
        campaign = NotificationCampaign.objects.create(
            name="promo", segment=NotificationCampaign.Segment.CITY,
            segment_params={'city': 'Accra'}, title='Deal', body='10% off',
            category='PROMO', notification_type=Notification.Type.PROMO, promo_code='SAVE10')
        CampaignService.run(campaign)
        n = Notification.objects.filter(campaign=campaign, user=self.accra).first()
        self.assertIsNotNone(n)
        self.assertEqual(n.promo_code, 'SAVE10')

    def test_conversion_attribution_credits_recent_campaign(self):
        campaign = NotificationCampaign.objects.create(
            name="conv", segment=NotificationCampaign.Segment.ALL, title='t', body='b',
            category='CAMPAIGN')
        notif = Notification.objects.create(
            user=self.accra, title='t', body='b', campaign=campaign)

        from decimal import Decimal
        CampaignService.attribute_conversion(self.accra, value=Decimal('80.00'))

        notif.refresh_from_db()
        campaign.refresh_from_db()
        self.assertIsNotNone(notif.converted_at)
        self.assertEqual(campaign.converted_count, 1)
        self.assertEqual(campaign.revenue_generated, Decimal('80.00'))

    def test_conversion_attribution_is_idempotent_per_notification(self):
        campaign = NotificationCampaign.objects.create(
            name="conv2", segment=NotificationCampaign.Segment.ALL, title='t', body='b',
            category='CAMPAIGN')
        Notification.objects.create(user=self.accra, title='t', body='b', campaign=campaign)
        from decimal import Decimal
        CampaignService.attribute_conversion(self.accra, value=Decimal('50'))
        # Second order with no new campaign notification → no double credit.
        CampaignService.attribute_conversion(self.accra, value=Decimal('50'))
        campaign.refresh_from_db()
        self.assertEqual(campaign.converted_count, 1)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_referral_preference_gates_push(self, mock_push):
        pref = NotificationService.get_preferences(self.accra)
        pref.referrals = False
        pref.save()
        NotificationService.notify_user(
            self.accra, title='Invite', body='Refer a friend', category='REFERRAL',
            type=Notification.Type.PROMO)
        mock_push.assert_not_called()

    @patch('marketplace.tasks.send_real_push.delay')
    def test_weekly_tips_preference_gates_push(self, mock_push):
        pref = NotificationService.get_preferences(self.accra)
        pref.weekly_tips = False
        pref.save()
        NotificationService.notify_user(
            self.accra, title='Tip', body='Care tip', category='WEEKLY_TIP',
            type=Notification.Type.SYSTEM)
        mock_push.assert_not_called()

    def test_preferences_api_exposes_new_toggles(self):
        self.client.force_authenticate(user=self.accra)
        r = self.client.get(reverse('notification-preferences'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('referrals', r.data['data'])
        self.assertIn('weekly_tips', r.data['data'])


@override_settings(EXPO_PUSH_ENABLED=True)
class PushDeferredUntilCommitTests(APITestCase):
    """Pushes must not escape a transaction that later rolls back.

    A push is not undoable: sending it mid-transaction can notify a customer
    about an event that never happened. Queue publication also waits until
    commit so workers never race an uncommitted row.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="oncommit@example.com", phone="233700000077", password="pw", role='CUSTOMER')
        PushDevice.objects.create(
            user=self.user, token="ExponentPushToken[COMMIT]", platform='android')

    @patch('marketplace.tasks.send_real_push.delay')
    def test_no_push_when_the_transaction_rolls_back(self, mock_push):
        from django.db import transaction

        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    NotificationService.notify_user(
                        self.user, title="t", body="b", category='ORDER',
                        type=Notification.Type.ORDER)
                    raise Boom()

        mock_push.assert_not_called()
        # The in-app row is rolled back with it, so nothing is left dangling.
        self.assertFalse(Notification.objects.filter(title="t").exists())

    @patch('marketplace.tasks.send_real_push.delay')
    def test_push_is_not_sent_before_commit(self, mock_push):
        from django.db import transaction

        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                NotificationService.notify_user(
                    self.user, title="inside", body="b", category='ORDER',
                    type=Notification.Type.ORDER)
                # Still inside the transaction — nothing may have gone out yet.
                mock_push.assert_not_called()

        mock_push.assert_called_once()


class PushPayloadTests(APITestCase):
    """The fields that decide whether a device actually alerts the customer.

    Regression: messages went out with no priority and no channelId, so
    Android filed them silently on a DEFAULT-importance channel and iOS was
    free to delay them. The in-app feed still filled up, which is why it
    looked like "notifications only work inside the app".
    """

    def _send(self, **kwargs):
        from marketplace.tasks import deliver_push
        with patch('marketplace.tasks.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.raise_for_status = lambda: None
            mock_post.return_value.json = lambda: {'data': [{'status': 'ok', 'id': 't-1'}]}
            deliver_push('t', 'b', {}, ['ExponentPushToken[PAYLOAD]'], **kwargs)
            return mock_post.call_args.kwargs['json'][0]

    def test_message_requests_high_priority(self):
        # Without this iOS sends at APNs priority 5 and may batch or drop it.
        self.assertEqual(self._send()['priority'], 'high')

    def test_message_targets_a_versioned_android_channel(self):
        message = self._send()
        # Versioned because Android freezes channel importance at creation.
        self.assertEqual(message['channelId'], 'default_v2')

    def test_orders_channel_is_used_for_transactional_pushes(self):
        from marketplace.tasks import ANDROID_CHANNEL_ORDERS, channel_for

        for category in ('ORDER', 'PAYMENT_SUCCESS', 'DELIVERY', 'PICKUP'):
            self.assertEqual(
                channel_for(category, Notification.Type.ORDER), ANDROID_CHANNEL_ORDERS)

    def test_marketing_stays_on_the_default_channel(self):
        from marketplace.tasks import ANDROID_CHANNEL_DEFAULT, channel_for

        self.assertEqual(
            channel_for('CAMPAIGN', Notification.Type.PROMO), ANDROID_CHANNEL_DEFAULT)

    def test_message_sets_an_ios_interruption_level(self):
        # Otherwise Focus modes / Notification Summary can hold it back.
        self.assertEqual(self._send()['interruptionLevel'], 'active')

    def test_message_carries_sound_and_ttl(self):
        message = self._send()
        self.assertEqual(message['sound'], 'default')
        self.assertEqual(message['ttl'], 86400)

    def test_badge_is_included_when_supplied(self):
        self.assertEqual(self._send(badge=7)['badge'], 7)

    def test_badge_is_omitted_when_unknown(self):
        self.assertNotIn('badge', self._send())


@override_settings(EXPO_PUSH_ENABLED=True)
class PushBadgeCountTests(APITestCase):
    """The badge must reflect unread count, so a killed app still shows it."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="badge@example.com", phone="233700000088", password="pw", role='CUSTOMER')
        PushDevice.objects.create(
            user=self.user, token="ExponentPushToken[BADGE]", platform='ios')

    @patch('marketplace.tasks.requests.post')
    def test_push_reports_the_users_unread_count(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {'data': [{'status': 'ok', 'id': 't-1'}]}

        # Two unread notifications already waiting.
        for index in range(2):
            Notification.objects.create(
                user=self.user, title=f'old {index}', body='b', is_read=False)

        with self.captureOnCommitCallbacks(execute=True):
            NotificationService.notify_user(
                self.user, title='new', body='b', category='ORDER',
                type=Notification.Type.ORDER)

        message = mock_post.call_args.kwargs['json'][0]
        # 2 existing + the one just created.
        self.assertEqual(message['badge'], 3)
        self.assertEqual(message['channelId'], 'orders_v2')


class PushDeviceLifecycleTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='device@example.com', phone='233700000090', password='pw', role='CUSTOMER')
        self.other = User.objects.create_user(
            email='device2@example.com', phone='233700000091', password='pw', role='CUSTOMER')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('notification-push-device')

    def register(self, token, device_id='install-1'):
        return self.client.post(self.url, {
            'token': token,
            'device_id': device_id,
            'platform': 'android',
            'app_version': '1.2.3',
        }, format='json')

    def test_token_rotation_retires_only_the_previous_install_token(self):
        old = 'ExpoPushToken[old-install-token]'
        new = 'ExpoPushToken[new-install-token]'
        other = PushDevice.objects.create(
            user=self.user,
            token='ExpoPushToken[other-device-token]',
            device_id='install-2',
            platform='android',
        )
        self.assertEqual(self.register(old).status_code, status.HTTP_200_OK)
        self.assertEqual(self.register(new).status_code, status.HTTP_200_OK)
        self.assertFalse(PushDevice.objects.get(token=old).is_active)
        self.assertTrue(PushDevice.objects.get(token=new).is_active)
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_logout_deactivates_only_the_matching_device(self):
        first = PushDevice.objects.create(
            user=self.user,
            token='ExpoPushToken[first-device-token]',
            device_id='install-1',
            platform='ios',
        )
        second = PushDevice.objects.create(
            user=self.user,
            token='ExpoPushToken[second-device-token]',
            device_id='install-2',
            platform='ios',
        )
        response = self.client.delete(self.url, {
            'token': first.token,
            'device_id': first.device_id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)

    def test_native_registration_rejects_a_non_expo_token(self):
        response = self.register('not-a-push-token')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reused_token_is_reassigned_to_the_current_account(self):
        token = 'ExpoPushToken[reassigned-token]'
        PushDevice.objects.create(
            user=self.other, token=token, device_id='install-1', platform='android',
            environment=settings.PUSH_ENVIRONMENT,
        )
        self.assertEqual(self.register(token).status_code, status.HTTP_200_OK)
        self.assertEqual(PushDevice.objects.get(token=token).user, self.user)

    def test_rebuilt_app_moves_a_globally_unique_token_to_current_environment(self):
        token = 'ExpoPushToken[cross-environment-reassignment]'
        PushDevice.objects.create(
            user=self.other,
            token=token,
            device_id='old-install',
            platform='android',
            environment=PushDevice.Environment.PRODUCTION,
        )

        self.assertEqual(self.register(token, 'new-install').status_code, status.HTTP_200_OK)
        device = PushDevice.objects.get(token=token)
        self.assertEqual(device.user, self.user)
        self.assertEqual(device.environment, settings.PUSH_ENVIRONMENT)
        self.assertEqual(device.device_id, 'new-install')
        self.assertEqual(PushDevice.objects.filter(token=token).count(), 1)


@override_settings(EXPO_PUSH_ENABLED=True)
class PushReceiptLifecycleTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='receipt@example.com', phone='233700000092', password='pw', role='CUSTOMER')
        self.device = PushDevice.objects.create(
            user=self.user,
            token='ExpoPushToken[receipt-device-token]',
            device_id='receipt-install',
            platform='android',
        )
        self.notification = Notification.objects.create(
            user=self.user,
            title='Order ready',
            body='Your laundry is ready',
            push_status=Notification.PushStatus.SENT,
        )
        self.delivery = PushDelivery.objects.create(
            notification=self.notification,
            device=self.device,
            ticket_id='ticket-1',
            status=PushDelivery.Status.TICKET_OK,
        )

    @patch('marketplace.tasks.fetch_push_receipts')
    def test_ok_receipt_marks_notification_delivered(self, mock_receipts):
        mock_receipts.return_value = {'ticket-1': {'status': 'ok'}}
        from marketplace.tasks import process_push_receipts
        self.assertEqual(process_push_receipts.run(str(self.notification.id)), 1)
        self.delivery.refresh_from_db()
        self.notification.refresh_from_db()
        self.assertEqual(self.delivery.status, PushDelivery.Status.RECEIPT_OK)
        self.assertEqual(self.notification.push_status, Notification.PushStatus.DELIVERED)
        self.assertIsNotNone(self.notification.delivered_at)

    @patch('marketplace.tasks.fetch_push_receipts')
    def test_device_not_registered_receipt_retires_token(self, mock_receipts):
        mock_receipts.return_value = {
            'ticket-1': {
                'status': 'error',
                'message': 'Device is not registered',
                'details': {'error': 'DeviceNotRegistered'},
            },
        }
        from marketplace.tasks import process_push_receipts
        self.assertEqual(process_push_receipts.run(str(self.notification.id)), 1)
        self.delivery.refresh_from_db()
        self.notification.refresh_from_db()
        self.device.refresh_from_db()
        self.assertEqual(self.delivery.status, PushDelivery.Status.RECEIPT_ERROR)
        self.assertEqual(self.notification.push_status, Notification.PushStatus.FAILED)
        self.assertFalse(self.device.is_active)

    @patch('marketplace.tasks.requests.post')
    def test_retry_does_not_send_a_second_push_for_an_accepted_ticket(self, mock_post):
        from marketplace.tasks import send_real_push
        self.assertEqual(send_real_push.run(str(self.notification.id)), 1)
        mock_post.assert_not_called()

    @patch('marketplace.tasks.requests.post')
    def test_send_ticket_is_persisted_per_device(self, mock_post):
        self.delivery.delete()
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            'data': [{'status': 'ok', 'id': 'ticket-new'}],
        }
        from marketplace.tasks import deliver_push
        sent = deliver_push(
            'Order ready',
            'Your laundry is ready',
            {},
            [self.device.token],
            notification=self.notification,
        )
        self.assertEqual(sent, 1)
        persisted = PushDelivery.objects.get(ticket_id='ticket-new')
        self.assertEqual(persisted.device, self.device)
        self.assertEqual(persisted.status, PushDelivery.Status.TICKET_OK)

    @patch('marketplace.tasks.fetch_push_receipts')
    def test_rate_limit_receipt_returns_notification_to_pending(self, mock_receipts):
        mock_receipts.return_value = {
            'ticket-1': {
                'status': 'error',
                'message': 'Rate exceeded',
                'details': {'error': 'MessageRateExceeded'},
            },
        }
        from marketplace.tasks import process_push_receipts

        self.assertEqual(process_push_receipts.run(str(self.notification.id)), 1)
        self.delivery.refresh_from_db()
        self.notification.refresh_from_db()
        self.assertEqual(self.delivery.retry_count, 1)
        self.assertEqual(self.notification.push_status, Notification.PushStatus.PENDING)
        self.assertIsNone(self.notification.push_last_queued_at)


@override_settings(EXPO_PUSH_ENABLED=True, PUSH_ENVIRONMENT='staging')
class PushRecoveryAndIsolationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='recovery@example.com', phone='233700000099', password='pw',
            role='CUSTOMER',
        )

    @patch('marketplace.tasks.send_real_push.delay')
    def test_pending_dispatcher_recovers_unqueued_rows(self, mock_delay):
        notification = Notification.objects.create(
            user=self.user,
            title='Pending push',
            body='Durable outbox row',
            push_status=Notification.PushStatus.PENDING,
        )
        from marketplace.tasks import dispatch_pending_pushes

        self.assertEqual(dispatch_pending_pushes.run(), 1)
        mock_delay.assert_called_once_with(str(notification.id))
        notification.refresh_from_db()
        self.assertIsNotNone(notification.push_last_queued_at)

    @patch('marketplace.tasks.requests.post')
    def test_sender_uses_only_current_environment_tokens(self, mock_post):
        PushDevice.objects.create(
            user=self.user,
            token='ExpoPushToken[staging-device]',
            environment=PushDevice.Environment.STAGING,
            platform=PushDevice.Platform.ANDROID,
        )
        PushDevice.objects.create(
            user=self.user,
            token='ExpoPushToken[production-device]',
            environment=PushDevice.Environment.PRODUCTION,
            platform=PushDevice.Platform.ANDROID,
        )
        notification = Notification.objects.create(
            user=self.user, title='Environment test', body='Only staging',
            push_status=Notification.PushStatus.PENDING,
        )
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            'data': [{'status': 'ok', 'id': 'staging-ticket'}],
        }
        from marketplace.tasks import send_real_push

        self.assertEqual(send_real_push.run(str(notification.id)), 1)
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual([message['to'] for message in payload], [
            'ExpoPushToken[staging-device]',
        ])

    def test_deduplication_survives_read_state_changes(self):
        first = NotificationService.notify_user(
            self.user, title='Order update', body='Ready',
            dedup_key='order:read-safe', push=False,
        )
        first.mark_as_read()
        second = NotificationService.notify_user(
            self.user, title='Order update', body='Ready',
            dedup_key='order:read-safe', push=False,
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, dedup_key='order:read-safe',
            ).count(),
            1,
        )
        self.assertEqual(NotificationEventClaim.objects.filter(
            user=self.user, dedup_key='order:read-safe',
        ).count(), 1)

    def test_registration_rejects_cross_environment_build(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(reverse('notification-push-device'), {
            'token': 'ExpoPushToken[wrong-environment]',
            'device_id': 'install-cross-env',
            'platform': 'android',
            'environment': 'production',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PushDevice.objects.filter(
            token='ExpoPushToken[wrong-environment]',
        ).exists())


@override_settings(EXPO_PUSH_ENABLED=True)
class AuthNotificationEventTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='authpush@example.com', phone='233700000120', password='pw', role='CUSTOMER')
        self.client.force_authenticate(user=self.user)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_signup_event_creates_customer_notification_and_queues_push(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('notification-auth-event'),
                {'event': 'SIGNUP_SUCCESS'},
                format='json',
                HTTP_X_DEVICE_ID='install-signup',
                HTTP_X_IDEMPOTENCY_KEY='signup-once',
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        notification = Notification.objects.get(user=self.user, category='SIGNUP_SUCCESS')
        self.assertEqual(notification.title, 'Welcome to Simame')
        self.assertEqual(notification.push_status, Notification.PushStatus.PENDING)
        mock_delay.assert_called_once_with(str(notification.id))

    @patch('marketplace.tasks.send_real_push.delay')
    def test_login_event_is_deduped_and_marks_first_device_sign_in(self, mock_delay):
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse('notification-auth-event'),
                    {'event': 'LOGIN_SUCCESS'},
                    format='json',
                    HTTP_X_DEVICE_ID='install-login',
                    HTTP_X_IDEMPOTENCY_KEY='login-once',
                )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(Notification.objects.filter(user=self.user, category='LOGIN_SUCCESS').count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.user, category='NEW_DEVICE_LOGIN').count(), 1)
        self.assertEqual(mock_delay.call_count, 2)


class SessionPushDeviceCleanupTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='cleanup@example.com', phone='233700000121', password='pw', role='CUSTOMER')
        self.session = DeviceSession.objects.create(
            user=self.user,
            device_id='install-cleanup',
            platform='android',
            app_version='1.0.0',
            user_agent='pytest',
            ip_address='127.0.0.1',
        )
        self.device = PushDevice.objects.create(
            user=self.user,
            token='ExpoPushToken[cleanup-token]',
            device_id='install-cleanup',
            platform=PushDevice.Platform.ANDROID,
        )

    def test_password_reset_keeps_security_push_device_active(self):
        from users.services.session_service import revoke_session

        revoke_session(self.session, reason='password_reset')
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_active)

    def test_logout_deactivates_matching_push_device(self):
        from users.services.session_service import revoke_session

        revoke_session(self.session, reason='logout')
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)


@override_settings(EXPO_PUSH_ENABLED=True)
class CustomerActivityNotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='activity@example.com', phone='233700000122', password='pw', role='CUSTOMER')
        self.client.force_authenticate(user=self.user)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_profile_updates_create_customer_notifications_without_accidental_dedup(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(reverse('auth_me'), {'first_name': 'Ama'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(reverse('auth_me'), {'last_name': 'Mensah'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        notifications = Notification.objects.filter(
            user=self.user,
            category='PROFILE_UPDATED',
        )
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(mock_delay.call_count, 2)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_address_crud_creates_customer_activity_notifications(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            create_response = self.client.post(reverse('address-list'), {
                'label': 'Home',
                'address_line1': '12 Ring Road',
                'city': 'Accra',
            }, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        address = Address.objects.get(user=self.user)

        with self.captureOnCommitCallbacks(execute=True):
            update_response = self.client.patch(
                reverse('address-detail', kwargs={'id': address.id}),
                {'address_line1': '14 Ring Road'},
                format='json',
            )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        with self.captureOnCommitCallbacks(execute=True):
            delete_response = self.client.delete(reverse('address-detail', kwargs={'id': address.id}))
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertTrue(Notification.objects.filter(user=self.user, category='ADDRESS_SAVED').exists())
        self.assertTrue(Notification.objects.filter(user=self.user, category='ADDRESS_UPDATED').exists())
        self.assertTrue(Notification.objects.filter(user=self.user, category='ADDRESS_REMOVED').exists())
        self.assertEqual(mock_delay.call_count, 3)
