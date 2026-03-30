# pyre-ignore[missing-module]
import pytest

# pyre-ignore[missing-module]
from django.urls import reverse

# pyre-ignore[missing-module]
from rest_framework import status

# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
class TestLoginRobustness:
    """
    Regression tests for login endpoint to prevent 500 errors
    and ensure predictable error responses.
    """

    def test_login_nonexistent_user_returns_400(self, client):
        """Should return 400 for nonexistent user, not 500 or 401."""
        r = client.post(
            reverse("auth_login"),
            {"email": "nonexistent@test.com", "password": "anypassword"},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid email or password" in str(r.json()["message"])

    def test_login_inactive_user_returns_400(self, client, authenticated_user):
        """Inactive user should return 400 with specific message."""
        authenticated_user.is_active = False
        authenticated_user.save()

        r = client.post(
            reverse("auth_login"),
            {"email": authenticated_user.email, "password": "password123"},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "disabled" in str(r.json()["message"]).lower()

    def test_login_missing_email_returns_400(self, client):
        """Missing email field should return 400 with validation error."""
        r = client.post(
            reverse("auth_login"),
            {"password": "password123"},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in str(r.json()).lower()

    def test_login_missing_password_returns_400(self, client, authenticated_user):
        """Missing password field should return 400 with validation error."""
        r = client.post(
            reverse("auth_login"),
            {"email": authenticated_user.email},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in str(r.json()).lower()

    def test_login_wrong_password_returns_400(self, client, authenticated_user):
        """Wrong password should return 400, not 500."""
        r = client.post(
            reverse("auth_login"),
            {"email": authenticated_user.email, "password": "wrongpassword"},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid email or password" in str(r.json()["message"])

    def test_login_admin_user_succeeds(self, client, admin_user):
        """Admin user login should return 200 with valid tokens."""
        r = client.post(
            reverse("auth_login"),
            {"email": admin_user.email, "password": "testpassword123"},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["success"] is True

        data = r.json()["data"]
        assert "accessToken" in data
        assert "refreshToken" in data
        assert data["user"]["email"] == admin_user.email
        assert data["user"]["role"] == "ADMIN"

    def test_login_regular_user_succeeds(self, client, authenticated_user):
        """Regular user login should return 200 with valid tokens."""
        r = client.post(
            reverse("auth_login"),
            {"email": authenticated_user.email, "password": "password123"},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["success"] is True

        data = r.json()["data"]
        assert "accessToken" in data
        assert "refreshToken" in data
        assert data["user"]["email"] == authenticated_user.email

    def test_login_response_envelope_consistency(self, client, authenticated_user):
        """All login responses should have consistent envelope structure."""
        r = client.post(
            reverse("auth_login"),
            {"email": authenticated_user.email, "password": "password123"},
            format="json",
        )

        json_data = r.json()
        assert "success" in json_data
        assert "message" in json_data
        assert "data" in json_data

        if r.status_code == status.HTTP_200_OK:
            assert json_data["success"] is True
            assert "accessToken" in json_data["data"]
        else:
            assert json_data["success"] is False
