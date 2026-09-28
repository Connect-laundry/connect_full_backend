"""Guards for the request paths the app hits most (discovery, detail, login).

Production talks to its database across a network, so every query is a round
trip. These tests pin query counts so an N+1 cannot creep back in, and cover
the cheaper login/sign-in paths and resized image URLs.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.hashers import PBKDF2PasswordHasher, check_password, make_password
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.opening_hours import HolidayOverride, OpeningHours
from laundries.models.review import Review
from laundries.models.service import LaundryService
from laundries.services.opening_status import (
    GHANA_TZ,
    get_laundry_opening_status,
    holiday_override_prefetch,
)
from ordering.models import LaunderableItem
from users.hashers import TunedPBKDF2PasswordHasher
from users.models import DeviceSession, SessionRefreshToken, User
from users.services import clerk_service
from users.services.session_service import issue_tokens_for_user
from utils.media import cloudinary_resized

LIST_URLS = [
    '/api/v1/laundries/featured/',
    '/api/v1/laundries/laundries/?nearby=true&radius=10&lat=5.6&lng=-0.18',
    '/api/v1/laundries/laundries/?recommended=true',
    '/api/v1/laundries/laundries/?cheapest=true',
]


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        email='perf-customer@example.com', phone='233555950002', password='StrongPass123!')


@pytest.fixture
def owner(db):
    return User.objects.create_user(
        email='perf-owner@example.com', phone='233555950001',
        password='StrongPass123!', role=User.Role.OWNER)


def _laundries(owner, customer, count, start=0):
    service_type, _ = Category.objects.get_or_create(
        name='Perf Wash', type=Category.CategoryType.SERVICE_TYPE)
    item, _ = LaunderableItem.objects.get_or_create(name='Perf Shirt')
    today = datetime.now(GHANA_TZ).date()
    created = []
    for i in range(start, start + count):
        laundry = Laundry.objects.create(
            name=f'Perf Laundry {i}', description='d', address='Accra',
            latitude=5.6 + i * 0.001, longitude=-0.18, phone_number=f'02400019{i:02d}',
            owner=owner, status=Laundry.ApprovalStatus.APPROVED, is_active=True,
            is_featured=True)
        for day in range(1, 8):
            OpeningHours.objects.create(
                laundry=laundry, day=day, opening_time=time(8), closing_time=time(20))
        HolidayOverride.objects.create(laundry=laundry, date=today + timedelta(days=3), is_closed=True)
        LaundryService.objects.create(
            laundry=laundry, item=item, service_type=service_type,
            price=Decimal('10.00'), is_available=True)
        Review.objects.create(laundry=laundry, user=customer, rating=4, comment='ok')
        created.append(laundry)
    return created


def _query_count(client, url):
    from django.core.cache import cache
    from logistics import models as logistics_models
    # Measure the cold path: opening status and the pricing config are cached.
    cache.clear()
    logistics_models._active_cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, response.content[:300]
    return len(ctx.captured_queries)


@pytest.mark.django_db
class TestDiscoveryQueryCounts:
    @pytest.mark.parametrize('url', LIST_URLS)
    def test_list_queries_do_not_grow_with_laundry_count(self, owner, customer, url):
        client = APIClient()
        client.force_authenticate(user=customer)

        _laundries(owner, customer, 2)
        few = _query_count(client, url)
        _laundries(owner, customer, 6, start=2)
        many = _query_count(client, url)

        assert many == few, f'{url}: {few} queries for 2 laundries, {many} for 8'
        assert many <= 6, f'{url}: {many} queries'

    @pytest.mark.parametrize('url', LIST_URLS)
    def test_lists_use_the_primary_queryset(self, owner, customer, url, caplog):
        # get_queryset falls back to a slower join-based queryset on any error,
        # which hides bugs (a shadowed import once sent every request there).
        _laundries(owner, customer, 2)
        Laundry.objects.create(
            name='Pending Laundry', description='d', address='Accra', latitude=5.6,
            longitude=-0.18, phone_number='0240001999', owner=owner,
            status=Laundry.ApprovalStatus.PENDING, is_active=True, is_featured=True)
        client = APIClient()
        client.force_authenticate(user=customer)

        with caplog.at_level('ERROR', logger='laundries.views.laundry'):
            response = client.get(url)

        assert 'Error in Laundry base queryset' not in caplog.text
        assert 'Pending Laundry' not in response.content.decode()

    def test_detail_query_count_is_bounded(self, owner, customer):
        laundry = _laundries(owner, customer, 1)[0]
        client = APIClient()
        client.force_authenticate(user=customer)

        assert _query_count(client, f'/api/v1/laundries/laundries/{laundry.id}/') <= 9


@pytest.mark.django_db
class TestPrefetchedOpeningStatus:
    def _status(self, laundry_id, now, prefetched):
        qs = Laundry.objects.filter(id=laundry_id).prefetch_related('opening_hours')
        if prefetched:
            qs = qs.prefetch_related(holiday_override_prefetch(now))
        return get_laundry_opening_status(qs.get(), now=now)

    def test_prefetched_overrides_match_direct_queries(self, owner, customer):
        laundry = _laundries(owner, customer, 1)[0]
        now = datetime(2026, 9, 28, 1, 0, tzinfo=GHANA_TZ)
        # Yesterday's overnight override still covers 01:00 today; tomorrow is closed.
        HolidayOverride.objects.create(
            laundry=laundry, date=now.date() - timedelta(days=1),
            opening_time=time(20), closing_time=time(2))
        HolidayOverride.objects.create(
            laundry=laundry, date=now.date() + timedelta(days=1), is_closed=True)

        for probe in [now, now.replace(hour=3), now.replace(hour=21)]:
            assert self._status(laundry.id, probe, True) == self._status(laundry.id, probe, False)
        assert self._status(laundry.id, now, True)['is_open_now'] is True
        after_close = self._status(laundry.id, now.replace(hour=21), True)
        assert after_close['is_open_now'] is False
        # Tomorrow's closure is skipped when finding the next opening.
        assert after_close['next_open_at'].startswith(str(now.date() + timedelta(days=2)))

    def test_prefetched_path_runs_no_override_queries(self, owner, customer):
        laundry = _laundries(owner, customer, 1)[0]
        now = datetime(2026, 9, 28, 21, 0, tzinfo=GHANA_TZ)
        loaded = (
            Laundry.objects.filter(id=laundry.id)
            .prefetch_related('opening_hours', holiday_override_prefetch(now))
            .get()
        )
        with CaptureQueriesContext(connection) as ctx:
            get_laundry_opening_status(loaded, now=now)
        assert len(ctx.captured_queries) == 0


class TestCloudinaryResized:
    def test_inserts_resize_for_versioned_upload(self):
        url = 'https://res.cloudinary.com/demo/image/upload/v1/media/laundries/shop_ab12'
        assert cloudinary_resized(url, 800) == (
            'https://res.cloudinary.com/demo/image/upload/c_limit,w_800,q_auto,f_auto/v1/media/laundries/shop_ab12'
        )

    @pytest.mark.parametrize('url', [
        None,
        '',
        'https://res.cloudinary.com/demo/image/upload/c_fill,w_100/v1/media/x',
        'https://example.com/media/laundries/x.png',
        '/media/laundries/x.png',
    ])
    def test_leaves_other_urls_unchanged(self, url):
        assert cloudinary_resized(url, 800) == url


class TestTunedPasswordHasher:
    @pytest.fixture(autouse=True)
    def _tuned_hasher(self, settings):
        settings.PASSWORD_HASHERS = ['users.hashers.TunedPBKDF2PasswordHasher']

    def test_uses_owasp_iteration_count(self):
        assert TunedPBKDF2PasswordHasher.iterations == 600_000
        assert TunedPBKDF2PasswordHasher.algorithm == PBKDF2PasswordHasher.algorithm
        assert make_password('StrongPass123!').startswith('pbkdf2_sha256$600000$')

    def test_existing_hashes_verify_and_are_upgraded(self):
        legacy = PBKDF2PasswordHasher().encode('StrongPass123!', 'legacysalt123456', iterations=1_200_000)
        upgraded = []

        assert check_password('StrongPass123!', legacy, setter=upgraded.append)
        assert upgraded == ['StrongPass123!']
        assert not check_password('wrong', legacy)


@pytest.mark.django_db
class TestLoginTokenIssue:
    def test_session_is_written_once_with_its_refresh_token(self, customer, rf):
        request = rf.post('/api/v1/auth/login/', HTTP_X_DEVICE_ID='device-1')
        with CaptureQueriesContext(connection) as ctx:
            tokens = issue_tokens_for_user(customer, request)

        session = DeviceSession.objects.get(user=customer)
        refresh = SessionRefreshToken.objects.get(session=session)
        assert tokens['access'] and tokens['refresh']
        assert session.device_id == 'device-1'
        assert session.current_refresh_jti == refresh.jti
        assert session.current_refresh_expires_at == refresh.expires_at
        assert not any(q['sql'].startswith('UPDATE "users_devicesession"') for q in ctx.captured_queries)


@pytest.mark.django_db
class TestClerkSignInProfileSource:
    @pytest.fixture
    def clerk_calls(self, settings, monkeypatch):
        settings.CLERK_SECRET_KEY = 'test-secret'
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'id': 'user_perf_1',
                    'primary_email_address_id': 'e1',
                    'email_addresses': [{
                        'id': 'e1', 'email_address': 'perf-social@example.com',
                        'verification': {'status': 'verified'},
                    }],
                    'external_accounts': [{'provider': 'oauth_google'}],
                }

        def fake_get(url, *, headers, timeout):
            calls.append(url)
            return FakeResponse()

        monkeypatch.setattr(clerk_service.requests, 'get', fake_get)
        monkeypatch.setattr(
            clerk_service.ClerkTokenVerifier, 'verify',
            lambda self, token: {'sub': 'user_perf_1', 'email': 'perf-social@example.com'},
        )
        return calls

    def test_first_sign_in_asks_clerk_for_verified_email(self, clerk_calls):
        user, created = clerk_service.authenticate_clerk_token('token')

        assert created is True
        assert user.email == 'perf-social@example.com'
        assert len(clerk_calls) == 1

    def test_returning_user_signs_in_from_token_claims(self, clerk_calls):
        clerk_service.authenticate_clerk_token('token')
        clerk_calls.clear()

        user, created = clerk_service.authenticate_clerk_token('token')

        assert created is False
        assert user.clerk_user_id == 'user_perf_1'
        assert clerk_calls == []
