"""Campaign Center (staff UI) — access control, compose, preview and sending."""
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from marketplace.models import Notification, NotificationCampaign, PushDevice
from marketplace.services.campaign_service import CampaignDispatchResult, CampaignService

User = get_user_model()
Status = NotificationCampaign.Status
Segment = NotificationCampaign.Segment


def _campaign(**overrides):
    defaults = dict(
        name='Rainy day promo',
        title='Beat the rain',
        body='20% off wash & fold today.',
        segment=Segment.ALL,
        category='CAMPAIGN',
    )
    defaults.update(overrides)
    return NotificationCampaign.objects.create(**defaults)


class CampaignCenterAccessTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', phone='233700001001', password='pw', is_staff=True)
        self.customer = User.objects.create_user(
            email='cust@example.com', phone='233700001002', password='pw')

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse('campaign-center'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_non_staff_cannot_open_campaign_center(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('campaign-center'))
        self.assertEqual(response.status_code, 302)

    def test_staff_can_open_campaign_center(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('campaign-center'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Campaign Center')

    def test_non_staff_cannot_send(self):
        campaign = _campaign()
        self.client.force_login(self.customer)
        response = self.client.post(reverse('campaign-center-send', args=[campaign.id]))
        self.assertEqual(response.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.DRAFT)


class CampaignComposeTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff2@example.com', phone='233700002001', password='pw', is_staff=True)
        self.client.force_login(self.staff)

    def _payload(self, **overrides):
        payload = {
            'name': 'Weekend push',
            'title': 'Weekend deal',
            'body': 'Book today and save.',
            'segment': Segment.ALL,
            'notification_type': Notification.Type.PROMO,
            'category': 'CAMPAIGN',
            'priority': Notification.Priority.NORMAL,
            'action_url': '',
            'image_url': '',
            'promo_code': '',
            'expires_at': '',
            'days': '',
            'hours': '',
            'city': '',
            'user_emails': '',
        }
        payload.update(overrides)
        return payload

    def test_creates_draft_and_records_author(self):
        response = self.client.post(reverse('campaign-center-new'), self._payload())
        self.assertEqual(response.status_code, 302)
        campaign = NotificationCampaign.objects.get(name='Weekend push')
        self.assertEqual(campaign.status, Status.DRAFT)
        self.assertEqual(campaign.created_by, self.staff)

    def test_city_segment_requires_a_city(self):
        response = self.client.post(
            reverse('campaign-center-new'), self._payload(segment=Segment.CITY))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NotificationCampaign.objects.exists())
        self.assertContains(response, 'A city is required')

    def test_city_segment_is_flattened_into_segment_params(self):
        self.client.post(
            reverse('campaign-center-new'),
            self._payload(segment=Segment.CITY, city='Kumasi'))
        campaign = NotificationCampaign.objects.get()
        self.assertEqual(campaign.segment_params, {'city': 'Kumasi'})

    def test_custom_segment_resolves_emails_to_user_ids(self):
        target = User.objects.create_user(
            email='target@example.com', phone='233700002099', password='pw')
        self.client.post(
            reverse('campaign-center-new'),
            self._payload(segment=Segment.CUSTOM, user_emails='target@example.com'))
        campaign = NotificationCampaign.objects.get()
        self.assertEqual(campaign.segment_params['user_ids'], [str(target.pk)])

    def test_custom_segment_rejects_unknown_email(self):
        response = self.client.post(
            reverse('campaign-center-new'),
            self._payload(segment=Segment.CUSTOM, user_emails='nobody@example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No account for')

    def test_inactive_segment_defaults_days_to_14(self):
        self.client.post(
            reverse('campaign-center-new'), self._payload(segment=Segment.INACTIVE))
        campaign = NotificationCampaign.objects.get()
        self.assertEqual(campaign.segment_params, {'inactive_days': 14})

    def test_sent_campaign_cannot_be_edited(self):
        campaign = _campaign(status=Status.SENT)
        response = self.client.get(reverse('campaign-center-edit', args=[campaign.id]))
        self.assertRedirects(
            response, reverse('campaign-center-detail', args=[campaign.id]))


class CampaignTemplateRenderTests(TestCase):
    """Every page must render — template syntax errors are invisible until hit."""

    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff5@example.com', phone='233700005001', password='pw', is_staff=True)
        self.client.force_login(self.staff)

    def test_list_page_renders(self):
        _campaign()
        response = self.client.get(reverse('campaign-center'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rainy day promo')

    def test_compose_page_renders_with_all_fields(self):
        response = self.client.get(reverse('campaign-center-new'))
        self.assertEqual(response.status_code, 200)
        for field in ('name', 'title', 'body', 'segment', 'promo_code', 'user_emails'):
            self.assertContains(response, f'name="{field}"')

    def test_detail_page_renders_with_send_controls(self):
        campaign = _campaign()
        response = self.client.get(reverse('campaign-center-detail', args=[campaign.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, campaign.title)
        self.assertContains(response, 'Send now')
        self.assertContains(response, 'Send test')

    def test_detail_hides_send_controls_once_sent(self):
        campaign = _campaign(status=Status.SENT)
        response = self.client.get(reverse('campaign-center-detail', args=[campaign.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Send now')

    def test_edit_page_repopulates_flattened_segment_params(self):
        campaign = _campaign(segment=Segment.CITY, segment_params={'city': 'Kumasi'})
        response = self.client.get(reverse('campaign-center-edit', args=[campaign.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kumasi')


class CampaignAudiencePreviewTests(TestCase):
    def setUp(self):
        # Role OWNER so the admin account itself is not part of the customer
        # segment — keeps the expected audience count unambiguous.
        self.staff = User.objects.create_user(
            email='staff3@example.com', phone='233700003001', password='pw',
            is_staff=True, role=User.Role.OWNER)
        self.client.force_login(self.staff)
        User.objects.create_user(email='c1@example.com', phone='233700003002', password='pw')
        User.objects.create_user(email='c2@example.com', phone='233700003003', password='pw')

    def test_returns_audience_count(self):
        response = self.client.get(reverse('campaign-center-audience'), {'segment': Segment.ALL})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 2)

    def test_rejects_unknown_segment(self):
        response = self.client.get(reverse('campaign-center-audience'), {'segment': 'NOPE'})
        self.assertEqual(response.status_code, 400)

    def test_city_segment_with_no_match_is_zero(self):
        response = self.client.get(
            reverse('campaign-center-audience'), {'segment': Segment.CITY, 'city': 'Atlantis'})
        self.assertEqual(response.json()['count'], 0)


@override_settings(EXPO_PUSH_ENABLED=True)
class CampaignSendTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff4@example.com', phone='233700004001', password='pw', is_staff=True)
        self.customer = User.objects.create_user(
            email='sendto@example.com', phone='233700004002', password='pw')
        PushDevice.objects.create(
            user=self.customer, token='ExponentPushToken[CENTER]', platform='android')
        self.client.force_login(self.staff)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_send_now_delivers_to_the_segment(self, _mock_push):
        campaign = _campaign()
        response = self.client.post(reverse('campaign-center-send', args=[campaign.id]))
        self.assertRedirects(
            response, reverse('campaign-center-detail', args=[campaign.id]))

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.SENT)
        self.assertTrue(
            Notification.objects.filter(campaign=campaign, user=self.customer).exists())

    def test_send_is_blocked_for_an_empty_segment(self):
        campaign = _campaign(segment=Segment.CITY, segment_params={'city': 'Atlantis'})
        self.client.post(reverse('campaign-center-send', args=[campaign.id]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.DRAFT)
        self.assertEqual(Notification.objects.filter(campaign=campaign).count(), 0)

    def test_send_requires_post(self):
        campaign = _campaign()
        response = self.client.get(reverse('campaign-center-send', args=[campaign.id]))
        self.assertEqual(response.status_code, 405)

    def test_schedule_rejects_a_past_time(self):
        campaign = _campaign()
        past = (timezone.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(
            reverse('campaign-center-schedule', args=[campaign.id]), {'scheduled_for': past})
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.DRAFT)
        self.assertIsNone(campaign.scheduled_for)

    def test_schedule_accepts_a_future_time(self):
        campaign = _campaign()
        future = (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(
            reverse('campaign-center-schedule', args=[campaign.id]), {'scheduled_for': future})
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.SCHEDULED)
        self.assertIsNotNone(campaign.scheduled_for)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_test_send_targets_one_user_without_touching_analytics(self, mock_push):
        campaign = _campaign()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('campaign-center-test', args=[campaign.id]),
                {'email': 'sendto@example.com'})
        self.assertRedirects(
            response, reverse('campaign-center-detail', args=[campaign.id]))

        mock_push.assert_called_once()
        notification = Notification.objects.get(user=self.customer)
        self.assertEqual(notification.title, campaign.title)
        # A test must never be attributed to the campaign or change its state.
        self.assertIsNone(notification.campaign)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.DRAFT)
        self.assertEqual(campaign.recipients_count, 0)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_test_send_is_repeatable(self, _mock_push):
        campaign = _campaign()
        url = reverse('campaign-center-test', args=[campaign.id])
        self.client.post(url, {'email': 'sendto@example.com'})
        self.client.post(url, {'email': 'sendto@example.com'})
        self.assertEqual(Notification.objects.filter(user=self.customer).count(), 2)

    def test_test_send_reports_unknown_email(self):
        campaign = _campaign()
        self.client.post(
            reverse('campaign-center-test', args=[campaign.id]),
            {'email': 'ghost@example.com'})
        # Signup emits its own welcome notifications, so assert on this
        # campaign's message specifically rather than the global count.
        self.assertFalse(Notification.objects.filter(title=campaign.title).exists())


@override_settings(EXPO_PUSH_ENABLED=True)
class CampaignDispatchTests(TestCase):
    """The single dispatcher shared by the API, the UI and the admin action."""

    def setUp(self):
        self.customer = User.objects.create_user(
            email='dispatch@example.com', phone='233700006001', password='pw')

    @patch('marketplace.tasks.send_real_push.delay')
    def test_queues_and_reports_the_audience(self, _mock_push):
        result = CampaignService.dispatch(_campaign())
        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, CampaignDispatchResult.QUEUED)
        self.assertEqual(result.audience, 1)

    def test_refuses_an_empty_segment(self):
        campaign = _campaign(segment=Segment.CITY, segment_params={'city': 'Atlantis'})
        result = CampaignService.dispatch(campaign)
        self.assertEqual(result.outcome, CampaignDispatchResult.EMPTY)
        campaign.refresh_from_db()
        # Status must be untouched so the campaign is not left mid-flight.
        self.assertEqual(campaign.status, Status.DRAFT)

    def test_refuses_a_campaign_already_sending(self):
        campaign = _campaign(status=Status.SENDING)
        result = CampaignService.dispatch(campaign)
        self.assertEqual(result.outcome, CampaignDispatchResult.ALREADY_SENDING)

    @patch('marketplace.tasks.requests.post')
    @patch('marketplace.tasks.run_campaign.delay')
    def test_small_campaign_delivers_inline_when_broker_is_down(self, mock_delay, mock_post):
        from kombu.exceptions import OperationalError
        mock_delay.side_effect = OperationalError('broker unreachable')
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {'data': [{'status': 'ok', 'id': 't-1'}]}

        campaign = _campaign()
        result = CampaignService.dispatch(campaign)

        self.assertTrue(result.ok)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.SENT)

    @override_settings(PUSH_INLINE_MAX_RECIPIENTS=0)
    @patch('marketplace.tasks.run_campaign.delay')
    def test_large_campaign_stays_scheduled_when_broker_is_down(self, mock_delay):
        from kombu.exceptions import OperationalError
        mock_delay.side_effect = OperationalError('broker unreachable')

        campaign = _campaign()
        result = CampaignService.dispatch(campaign)

        self.assertEqual(result.outcome, CampaignDispatchResult.UNAVAILABLE)
        campaign.refresh_from_db()
        # Left SCHEDULED so the worker/beat picks it up once the broker returns.
        self.assertEqual(campaign.status, Status.SCHEDULED)


@override_settings(EXPO_PUSH_ENABLED=True)
class CampaignAdminActionTests(TestCase):
    """`send_now` in the Django model admin must match the Campaign Center."""

    def setUp(self):
        from marketplace.admin import NotificationCampaignAdmin

        self.staff = User.objects.create_user(
            email='adminaction@example.com', phone='233700007001', password='pw',
            is_staff=True, role=User.Role.OWNER)
        self.customer = User.objects.create_user(
            email='admintarget@example.com', phone='233700007002', password='pw')
        self.admin = NotificationCampaignAdmin(NotificationCampaign, AdminSite())

    def _request(self):
        request = RequestFactory().post('/admin/marketplace/notificationcampaign/')
        request.user = self.staff
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    @patch('marketplace.tasks.send_real_push.delay')
    def test_action_sends_a_valid_campaign(self, _mock_push):
        campaign = _campaign()
        self.admin.send_now(
            self._request(), NotificationCampaign.objects.filter(pk=campaign.pk))

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.SENT)
        self.assertTrue(
            Notification.objects.filter(campaign=campaign, user=self.customer).exists())

    def test_action_skips_an_empty_segment(self):
        campaign = _campaign(segment=Segment.CITY, segment_params={'city': 'Atlantis'})
        self.admin.send_now(
            self._request(), NotificationCampaign.objects.filter(pk=campaign.pk))

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.DRAFT)
        self.assertEqual(Notification.objects.filter(campaign=campaign).count(), 0)

    def test_action_skips_a_campaign_already_sending(self):
        campaign = _campaign(status=Status.SENDING)
        self.admin.send_now(
            self._request(), NotificationCampaign.objects.filter(pk=campaign.pk))

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.SENDING)
        self.assertEqual(Notification.objects.filter(campaign=campaign).count(), 0)

    @patch('marketplace.tasks.requests.post')
    @patch('marketplace.tasks.run_campaign.delay')
    def test_action_delivers_inline_when_broker_is_down(self, mock_delay, mock_post):
        from kombu.exceptions import OperationalError
        mock_delay.side_effect = OperationalError('broker unreachable')
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {'data': [{'status': 'ok', 'id': 't-1'}]}

        campaign = _campaign()
        self.admin.send_now(
            self._request(), NotificationCampaign.objects.filter(pk=campaign.pk))

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.SENT)

    @patch('marketplace.tasks.send_real_push.delay')
    def test_action_handles_a_mixed_selection(self, _mock_push):
        good = _campaign(name='Good one')
        empty = _campaign(name='Empty one', segment=Segment.CITY,
                          segment_params={'city': 'Atlantis'})

        self.admin.send_now(
            self._request(),
            NotificationCampaign.objects.filter(pk__in=[good.pk, empty.pk]))

        good.refresh_from_db()
        empty.refresh_from_db()
        self.assertEqual(good.status, Status.SENT)
        self.assertEqual(empty.status, Status.DRAFT)
