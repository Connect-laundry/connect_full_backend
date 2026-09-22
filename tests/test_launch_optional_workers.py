from unittest.mock import Mock, patch

import pytest

from utils.tasks import safe_task_delay


def test_critical_task_runs_when_broker_accepts_but_no_worker_exists(settings):
    settings.CRITICAL_TASKS_USE_CELERY = False
    task = Mock(name='critical_email')
    assert safe_task_delay(task, 'customer', fallback_sync=True)
    task.delay.assert_not_called()
    task.apply.assert_called_once_with(args=('customer',), kwargs={}, throw=True)


def test_failed_inline_task_is_not_reported_as_delivered(settings):
    settings.CRITICAL_TASKS_USE_CELERY = False
    task = Mock()
    task.apply.side_effect = RuntimeError('provider unavailable')
    assert not safe_task_delay(task, fallback_sync=True)


@pytest.mark.django_db
def test_push_does_not_depend_on_worker_or_broker(settings):
    from marketplace.tasks import dispatch_claimed_push
    settings.PUSH_USE_CELERY = False
    with patch('marketplace.tasks.send_real_push.delay') as queue, \
         patch('marketplace.tasks.send_push_directly', return_value=1) as direct:
        assert dispatch_claimed_push('notification-id')
        queue.assert_not_called()
        direct.assert_called_once_with('notification-id')


@pytest.mark.django_db
def test_first_order_promotion_requires_verified_email(authenticated_user, settings):
    settings.PROMO_REQUIRE_VERIFIED_EMAIL = True
    from ordering.models.coupons import Coupon
    coupon = Coupon.objects.create(code='VERIFIEDFIRST', discount_value=5, first_time_users_only=True)
    assert coupon.is_valid(user=authenticated_user)[0] is False
    authenticated_user.email_verified = True
    authenticated_user.save(update_fields=['email_verified'])
    assert coupon.is_valid(user=authenticated_user)[0] is True


@pytest.mark.django_db
def test_unverified_user_cannot_apply_referral(auth_client, authenticated_user, settings):
    settings.PROMO_REQUIRE_VERIFIED_EMAIL = True
    from django.urls import reverse
    from users.models import User
    referrer = User.objects.create_user(email='referrer@qa.test', phone='233200002212', password='StrongPass123!', referral_code='VERIFIEDREF')
    response = auth_client.post(reverse('referral_apply'), {'referral_code': referrer.referral_code}, format='json')
    assert response.status_code == 400
    authenticated_user.refresh_from_db()
    assert authenticated_user.referred_by_id is None


def test_direct_mode_does_not_contact_broker_for_receipts(settings):
    from marketplace.tasks import _schedule_push_receipts
    settings.PUSH_USE_CELERY = False
    settings.CELERY_TASK_ALWAYS_EAGER = False
    with patch('marketplace.tasks.process_push_receipts.apply_async') as queue:
        assert _schedule_push_receipts('notification-id') is False
        queue.assert_not_called()


@pytest.mark.django_db
def test_verified_email_gate_is_off_by_default(authenticated_user):
    """Email/password sign-ups have no verification step yet; the default must
    not silently remove first-order promotions from every such customer."""
    from ordering.models.coupons import Coupon
    coupon = Coupon.objects.create(code='DEFAULTFIRST', discount_value=5, first_time_users_only=True)
    assert authenticated_user.email_verified is False
    assert coupon.is_valid(user=authenticated_user)[0] is True
