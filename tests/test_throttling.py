"""Shared-network-friendly rate limiting (see config/throttling.py).

Scenarios mirror the launch reality in Ghana: campus Wi-Fi, hostels and
carrier-grade NAT put many legitimate customers behind one public IP.
"""
import itertools
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from users.models import User

_seq = itertools.count()


def _signup(client, *, email=None, **meta):
    n = next(_seq)
    return client.post(reverse('auth_register'), data={
        'email': email or f'nat-{n}@example.com',
        'phone': f'2332{n:08d}',
        'first_name': 'Nat', 'last_name': f'User{n}',
        'password': 'StrongPass123!', 'password_confirm': 'StrongPass123!',
    }, format='json', **meta)


class _Clock:
    """Deterministic time for throttle windows."""

    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def _fresh_counters():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def clock():
    c = _Clock()
    with patch.object(SimpleRateThrottle, 'timer', staticmethod(c)):
        yield c


SAME_IP = {'REMOTE_ADDR': '41.66.200.10'}


@pytest.mark.django_db
class TestSharedNetworkSignup:
    def test_scenario_a_25_customers_behind_one_ip(self, client):
        # The old rule (10/hour per IP) failed customer 11.
        statuses = [_signup(client, **SAME_IP).status_code for _ in range(25)]
        assert statuses == [status.HTTP_201_CREATED] * 25

    def test_scenario_b_50_customers_signing_up_together(self, client):
        statuses = [_signup(client, **SAME_IP).status_code for _ in range(50)]
        assert statuses.count(status.HTTP_201_CREATED) == 50

    def test_scenario_c_100_customers_over_an_hour(self, client, clock):
        results = []
        for _ in range(100):
            results.append(_signup(client, **SAME_IP).status_code)
            clock.now += 36  # 100 sign-ups spread across 60 minutes
        assert results.count(status.HTTP_201_CREATED) == 100

    def test_scenario_d_scripted_flood_is_stopped(self, client, clock):
        codes = [_signup(client, **SAME_IP).status_code for _ in range(70)]
        assert codes[:60] == [status.HTTP_201_CREATED] * 60
        assert set(codes[60:]) == {status.HTTP_429_TOO_MANY_REQUESTS}

    def test_scenario_d_flood_hits_the_hourly_ceiling_even_when_paced(self, client, clock):
        codes = []
        for _ in range(320):
            codes.append(_signup(client, **SAME_IP).status_code)
            clock.now += 6  # 50/5m: under the burst limit, 320 in 32 minutes
        assert codes.count(status.HTTP_201_CREATED) == 300
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS

    def test_scenario_e_many_ips_targeting_one_email_are_limited_per_email(self, client):
        codes = [
            _signup(client, email='victim@example.com', REMOTE_ADDR=f'102.176.0.{i}').status_code
            for i in range(1, 8)
        ]
        # 5 attempts/hour per address, whatever IP they come from.
        assert status.HTTP_429_TOO_MANY_REQUESTS in codes
        assert codes.index(status.HTTP_429_TOO_MANY_REQUESTS) == 5

    def test_scenario_f_attacker_on_shared_ip_only_delays_neighbours_briefly(self, client, clock):
        for _ in range(60):
            _signup(client, **SAME_IP)
        blocked = _signup(client, **SAME_IP)
        assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        # A customer on another network is unaffected.
        assert _signup(client, REMOTE_ADDR='154.160.1.1').status_code == status.HTTP_201_CREATED
        # The shared IP recovers after the short burst window, not an hour.
        clock.now += 301
        assert _signup(client, **SAME_IP).status_code == status.HTTP_201_CREATED

    def test_429_is_human_and_machine_readable(self, client):
        for _ in range(60):
            _signup(client, **SAME_IP)
        response = _signup(client, **SAME_IP)
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        body = response.json()
        assert body['message'].startswith("We're receiving many sign-ups right now")
        assert int(response['Retry-After']) >= 1
        assert body['data']['retry_after'] == int(response['Retry-After'])
        for jargon in ('429', 'throttl', 'rate limit', 'rate_limited'):
            assert jargon not in body['message'].lower()


@pytest.fixture
def render_proxies(settings):
    settings.CLIENT_IP_HEADER = 'HTTP_TRUE_CLIENT_IP'
    settings.TRUSTED_PROXY_COUNT = 3


@pytest.mark.django_db
@pytest.mark.usefixtures('render_proxies')
class TestProxyIdentity:
    def test_forged_x_forwarded_for_does_not_bypass_ip_limits(self, client):
        codes = [
            _signup(client, HTTP_TRUE_CLIENT_IP='41.66.200.10',
                    HTTP_X_FORWARDED_FOR=f'203.0.113.{i}, 41.66.200.10, 104.22.160.34, 10.198.0.1',
                    REMOTE_ADDR='127.0.0.1').status_code
            for i in range(62)
        ]
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS

    def test_renders_rotating_internal_hop_does_not_split_one_customer(self, client):
        codes = [
            _signup(client, HTTP_X_FORWARDED_FOR=f'41.66.200.10, 104.22.160.34, 10.19{i % 9}.0.{i}',
                    REMOTE_ADDR='127.0.0.1').status_code
            for i in range(62)
        ]
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS

    def test_render_proxy_address_does_not_merge_all_customers(self, client):
        # Every request reaches Django from 127.0.0.1; distinct customers must
        # still get distinct buckets.
        codes = [
            _signup(client, HTTP_TRUE_CLIENT_IP=f'41.66.{i // 250}.{i % 250 + 1}', REMOTE_ADDR='127.0.0.1').status_code
            for i in range(80)
        ]
        assert codes.count(status.HTTP_201_CREATED) == 80


@pytest.mark.django_db
class TestResilienceAndConfig:
    def test_counter_store_outage_allows_signup_and_is_logged(self, client, caplog):
        with patch.object(SimpleRateThrottle, 'cache') as broken:
            broken.get.side_effect = ConnectionError('redis down')
            response = _signup(client, **SAME_IP)
        assert response.status_code == status.HTTP_201_CREATED
        assert any('rate limiting degraded' in r.getMessage() for r in caplog.records)

    def test_invalid_env_rate_falls_back_to_default_without_crashing(self, monkeypatch):
        from config import settings as project_settings
        monkeypatch.setenv('SIGNUP_IP_BURST_RATE', 'lots-per-minute')
        rates = project_settings._resolve_throttle_rates()
        assert rates['signup_ip_burst'] == project_settings.THROTTLE_RATE_DEFAULTS['signup_ip_burst'][1]

    def test_every_rate_is_env_configurable_and_parseable(self):
        from config import settings as project_settings
        from config.rate_parsing import parse_rate
        for scope, (env_name, default) in project_settings.THROTTLE_RATE_DEFAULTS.items():
            assert env_name and not env_name.startswith('EXPO_PUBLIC'), scope
            assert parse_rate(default)[0] > 0

    @pytest.mark.parametrize('rate, expected', [
        ('30/5m', (30, 300)), ('150/h', (150, 3600)), ('10/minute', (10, 60)), ('1000/d', (1000, 86400)),
    ])
    def test_rate_formats(self, rate, expected):
        from config.rate_parsing import parse_rate
        assert parse_rate(rate) == expected


@pytest.mark.django_db
class TestPerIdentityLimits:
    def _user(self, email='limits@example.com'):
        return User.objects.create_user(email=email, phone=f'2335{next(_seq):08d}', password='StrongPass123!')

    def test_refresh_is_limited_per_token_not_per_shared_ip(self, client):
        # 40 customers behind one carrier IP refreshing at once: all served.
        codes = [
            client.post(reverse('token_refresh'), {'refresh': f'token-{i}'}, format='json', **SAME_IP).status_code
            for i in range(40)
        ]
        assert status.HTTP_429_TOO_MANY_REQUESTS not in codes
        # One client hammering the same token is stopped.
        same = [
            client.post(reverse('token_refresh'), {'refresh': 'stuck-token'}, format='json', **SAME_IP).status_code
            for _ in range(12)
        ]
        assert same[-1] == status.HTTP_429_TOO_MANY_REQUESTS

    def test_password_reset_email_is_limited_per_address(self, client):
        self._user('reset@example.com')
        codes = [
            client.post(reverse('auth_forgot_password'), {'email': 'reset@example.com'}, format='json', **SAME_IP)
            for _ in range(4)
        ]
        assert [c.status_code for c in codes[:3]] == [status.HTTP_200_OK] * 3
        assert codes[3].status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert codes[3].json()['message'].startswith('You can request another reset email in')
        # Neighbours on the same network can still reset their own passwords.
        other = client.post(reverse('auth_forgot_password'), {'email': 'someone-else@example.com'},
                            format='json', **SAME_IP)
        assert other.status_code == status.HTTP_200_OK

    def test_password_reset_does_not_reveal_whether_an_account_exists(self, client):
        self._user('exists@example.com')
        known = client.post(reverse('auth_forgot_password'), {'email': 'exists@example.com'}, format='json')
        unknown = client.post(reverse('auth_forgot_password'), {'email': 'ghost@example.com'}, format='json')
        assert known.status_code == unknown.status_code
        assert known.json()['message'] == unknown.json()['message']

    def test_coupon_guessing_is_limited_per_user(self):
        api = APIClient()
        api.force_authenticate(self._user())
        codes = [
            api.post(reverse('coupon-validate'), {'code': f'GUESS{i}', 'order_value': '50.00'}, format='json').status_code
            for i in range(11)
        ]
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS
        assert status.HTTP_429_TOO_MANY_REQUESTS not in codes[:10]

    def test_review_limit_is_enforced(self):
        from laundries.views.review import ReviewCreateView
        from config.throttling import ReviewThrottle
        assert ReviewThrottle in ReviewCreateView.throttle_classes


@pytest.mark.django_db
class TestReferralAbuse:
    def _pair(self):
        a = User.objects.create_user(email=f'ref-a{next(_seq)}@example.com', phone=f'2336{next(_seq):08d}',
                                     password='StrongPass123!', first_name='Ama', last_name='Mensah')
        b = User.objects.create_user(email=f'ref-b{next(_seq)}@example.com', phone=f'2336{next(_seq):08d}',
                                     password='StrongPass123!')
        for user in (a, b):
            user.referral_code = f'REF{next(_seq):06d}'
            user.save(update_fields=['referral_code'])
        return a, b

    def _apply(self, user, code):
        api = APIClient()
        api.force_authenticate(user)
        return api.post(reverse('referral_apply'), {'referral_code': code}, format='json')

    def test_success_never_reveals_the_referrers_identity(self):
        a, b = self._pair()
        response = self._apply(b, a.referral_code)
        assert response.status_code == status.HTTP_200_OK
        text = str(response.json())
        assert a.email not in text and 'Ama' not in text and 'Mensah' not in text

    def test_referral_loops_and_self_referral_are_rejected(self):
        a, b = self._pair()
        assert self._apply(b, a.referral_code).status_code == status.HTTP_200_OK
        assert self._apply(a, b.referral_code).status_code == status.HTTP_400_BAD_REQUEST
        assert self._apply(a, a.referral_code).status_code == status.HTTP_400_BAD_REQUEST

    def test_referral_code_guessing_is_limited(self):
        _a, b = self._pair()
        codes = [self._apply(b, f'NOPE{i}').status_code for i in range(6)]
        assert codes[-1] == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestFeedbackThrottle:
    def test_feedback_throttle(self, auth_client):
        url = reverse('feedback')
        for _ in range(3):
            response = auth_client.post(url, data={"subject": "test", "message": "test"})
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS
        response = auth_client.post(url, data={"subject": "test", "message": "test"})
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
