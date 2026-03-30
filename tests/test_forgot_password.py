import pytest
from django.urls import reverse
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from users.models import User, PasswordResetToken


@pytest.fixture(autouse=True)
def reset_throttle_and_email(settings):
    """Clear throttle cache and use in-memory email backend for every test."""
    cache.clear()
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    yield
    cache.clear()


@pytest.mark.django_db
class TestForgotPassword:

    def test_forgot_password_success(self, client):
        """Known email: creates token, returns 200, sends email."""
        user = User.objects.create_user(
            email="test@example.com", phone="1234567890", password="old-password"
        )
        url = reverse("auth_forgot_password")

        response = client.post(
            url, {"email": "test@example.com"}, content_type="application/json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert (
            "you will receive a password reset link shortly" in response.data["message"]
        )
        assert PasswordResetToken.objects.filter(user=user).exists()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["test@example.com"]
        assert "Reset Your Connect Laundry Password" in mail.outbox[0].subject

    def test_forgot_password_unknown_email_no_leak(self, client):
        """Unknown email still returns 200 — enumeration protection."""
        url = reverse("auth_forgot_password")
        response = client.post(
            url, {"email": "ghost@example.com"}, content_type="application/json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert (
            "you will receive a password reset link shortly" in response.data["message"]
        )
        assert len(mail.outbox) == 0

    def test_reset_password_success(self, client):
        """Valid token resets password and marks token as used."""
        user = User.objects.create_user(
            email="test@example.com", phone="1234567890", password="old-password"
        )
        raw_token = PasswordResetToken.create_for_user(user)

        url = reverse("auth_reset_password")
        response = client.post(
            url,
            {
                "token": raw_token,
                "new_password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            },
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["message"] == "Password successfully reset."

        user.refresh_from_db()
        assert user.check_password("NewStrongPass123!")

        token_record = PasswordResetToken.objects.get(user=user)
        assert token_record.used_at is not None

    def test_reset_password_invalid_token(self, client):
        """Completely invalid token returns 400."""
        url = reverse("auth_reset_password")
        response = client.post(
            url,
            {
                "token": "invalid-garbage-token",
                "new_password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            },
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid or expired token" in response.data["detail"]

    def test_reset_password_password_mismatch(self, client):
        """Mismatched passwords return 400 before even checking token."""
        url = reverse("auth_reset_password")
        response = client.post(
            url,
            {
                "token": "any-token",
                "new_password": "NewStrongPass123!",
                "confirm_password": "DifferentPass123!",
            },
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Passwords do not match" in str(response.data)

    def test_reset_password_expired_token(self, client):
        """Expired token returns 400."""
        user = User.objects.create_user(
            email="test@example.com", phone="1234567890", password="old-password"
        )
        raw_token = PasswordResetToken.create_for_user(user)

        # Force expire the token
        token_record = PasswordResetToken.objects.get(user=user)
        token_record.expires_at = timezone.now() - timedelta(hours=2)
        token_record.save()

        url = reverse("auth_reset_password")
        response = client.post(
            url,
            {
                "token": raw_token,
                "new_password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            },
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid or expired token" in response.data["detail"]

    def test_reset_password_already_used_token(self, client):
        """Already-used token returns 400."""
        user = User.objects.create_user(
            email="test@example.com", phone="1234567890", password="old-password"
        )
        raw_token = PasswordResetToken.create_for_user(user)

        # Mark as already used
        token_record = PasswordResetToken.objects.get(user=user)
        token_record.used_at = timezone.now() - timedelta(minutes=5)
        token_record.save()

        url = reverse("auth_reset_password")
        response = client.post(
            url,
            {
                "token": raw_token,
                "new_password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            },
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid or expired token" in response.data["detail"]

    def test_forgot_password_throttle(self, client):
        """Exceeding password_reset throttle (3/hour) returns 429."""
        url = reverse("auth_forgot_password")
        data = {"email": "test@example.com"}

        for _ in range(3):
            client.post(url, data, content_type="application/json")

        response = client.post(url, data, content_type="application/json")
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
