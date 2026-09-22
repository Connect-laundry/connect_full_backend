"""Password reset end to end, as a customer experiences it.

Production 2026-09-22: the API reported success, but the emailed link pointed
at a domain that does not exist, and the lookup missed differently-cased
emails. These tests follow the real email: its link must open a working page
on this backend, and its code must reset the password exactly once.
"""
import re

import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from users.models import User


@pytest.fixture(autouse=True)
def email_and_throttles(settings):
    cache.clear()
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    settings.PASSWORD_RESET_PAGE_URL = ''
    yield
    cache.clear()


def _request_reset(client, email):
    response = client.post(reverse('auth_forgot_password'), {'email': email}, content_type='application/json')
    assert response.status_code == 200
    assert len(mail.outbox) == 1, 'the reset email must be sent'
    body = mail.outbox[0].body
    link = re.search(r'https?://\S*reset-password/\?resetId=[0-9a-f-]+', body).group(0)
    code = re.search(r'enter this one-time reset code:\s*\n+\s*(\S+)', body).group(1)
    return link, code


@pytest.mark.django_db
def test_emailed_link_opens_the_hosted_page_and_resets_once(client):
    user = User.objects.create_user(email='Reset.Me@Example.com', phone='233200000901', password='Old-Pass-12345')
    link, code = _request_reset(client, 'reset.me@example.com')  # typed in a different case
    assert mail.outbox[0].to == [user.email]
    assert link.startswith('http://testserver/reset-password/'), 'link must point at this backend'

    path = link.replace('http://testserver', '')
    page = client.get(path)
    assert page.status_code == 200
    assert b'Reset your password' in page.content
    assert b'connect-laundry://authScreens/resetPassword?resetId=' in page.content

    reset_id = re.search(r'resetId=([0-9a-f-]+)', link).group(1)
    form = {'reset_id': reset_id, 'token': code, 'new_password': 'New-Pass-67890!', 'confirm_password': 'New-Pass-67890!'}
    done = client.post('/reset-password/', form)
    assert done.status_code == 200
    assert b'Password updated' in done.content

    login = client.post(reverse('auth_login'), {'email': 'Reset.Me@Example.com', 'password': 'New-Pass-67890!'}, content_type='application/json')
    assert login.status_code == 200
    old = client.post(reverse('auth_login'), {'email': 'Reset.Me@Example.com', 'password': 'Old-Pass-12345'}, content_type='application/json')
    assert old.status_code == 401

    again = client.post('/reset-password/', {**form, 'new_password': 'Other-Pass-111!', 'confirm_password': 'Other-Pass-111!'})
    assert again.status_code == 400
    assert b'invalid or has expired' in again.content


@pytest.mark.django_db
def test_hosted_page_shows_readable_errors(client):
    User.objects.create_user(email='page@example.com', phone='233200000902', password='Old-Pass-12345')
    link, code = _request_reset(client, 'page@example.com')
    reset_id = re.search(r'resetId=([0-9a-f-]+)', link).group(1)
    mismatch = client.post('/reset-password/', {'reset_id': reset_id, 'token': code, 'new_password': 'New-Pass-67890!', 'confirm_password': 'different'})
    assert mismatch.status_code == 400
    assert b'Passwords do not match' in mismatch.content
    wrong = client.post('/reset-password/', {'reset_id': reset_id, 'token': 'WRONG', 'new_password': 'New-Pass-67890!', 'confirm_password': 'New-Pass-67890!'})
    assert wrong.status_code == 400
    assert b'invalid or has expired' in wrong.content


@pytest.mark.django_db
def test_app_reset_screen_path_uses_the_same_code(client):
    """The mobile resetPassword screen posts resetId + code to the API."""
    User.objects.create_user(email='app@example.com', phone='233200000903', password='Old-Pass-12345')
    link, code = _request_reset(client, 'app@example.com')
    reset_id = re.search(r'resetId=([0-9a-f-]+)', link).group(1)
    response = client.post(reverse('auth_reset_password'), {
        'reset_id': reset_id, 'token': code, 'new_password': 'New-Pass-67890!', 'confirm_password': 'New-Pass-67890!',
    }, content_type='application/json')
    assert response.status_code == 200
    login = client.post(reverse('auth_login'), {'email': 'app@example.com', 'password': 'New-Pass-67890!'}, content_type='application/json')
    assert login.status_code == 200


@pytest.mark.django_db
def test_reset_email_is_sent_without_any_worker(client, settings):
    """No broker, no worker, eager off: the email must still go out."""
    from config.celery import app as celery_app
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CRITICAL_TASKS_USE_CELERY = False
    celery_app.conf.update(task_always_eager=False, broker_url='redis://127.0.0.1:6399/1')
    try:
        User.objects.create_user(email='noworker@example.com', phone='233200000904', password='Old-Pass-12345')
        _request_reset(client, 'noworker@example.com')
    finally:
        celery_app.conf.update(task_always_eager=True)


@pytest.mark.django_db
def test_login_matches_email_case_insensitively(client):
    """Accounts created outside the app keep capitals; the app sends lowercase."""
    User.objects.create_user(email='AppReview@Example.com', phone='233200000905', password='Review-Pass-123')
    for typed in ('appreview@example.com', 'APPREVIEW@EXAMPLE.COM', ' AppReview@example.com '):
        response = client.post(reverse('auth_login'), {'email': typed.strip(), 'password': 'Review-Pass-123'}, content_type='application/json')
        assert response.status_code == 200, typed
    wrong = client.post(reverse('auth_login'), {'email': 'appreview@example.com', 'password': 'nope'}, content_type='application/json')
    assert wrong.status_code == 401


@pytest.mark.django_db
def test_hosted_page_rejects_cross_site_posts():
    """A form post without the page's CSRF token is refused."""
    from django.test import Client
    User.objects.create_user(email='csrf@example.com', phone='233200000906', password='Old-Pass-12345')
    strict = Client(enforce_csrf_checks=True)
    link, code = _request_reset(strict, 'csrf@example.com')
    reset_id = re.search(r'resetId=([0-9a-f-]+)', link).group(1)
    form = {'reset_id': reset_id, 'token': code, 'new_password': 'New-Pass-67890!', 'confirm_password': 'New-Pass-67890!'}
    forged = strict.post('/reset-password/', form)
    assert forged.status_code == 403
    page = strict.get(link.replace('http://testserver', ''))
    token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', page.content).group(1).decode()
    ok = strict.post('/reset-password/', {**form, 'csrfmiddlewaretoken': token})
    assert ok.status_code == 200 and b'Password updated' in ok.content
