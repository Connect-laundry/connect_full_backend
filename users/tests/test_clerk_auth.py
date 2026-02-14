import json
import pytest
from unittest.mock import patch, MagicMock
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient
from jose import jwt

User = get_user_model()

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def mock_jwks():
    return {
        "keys": [
            {
                "kid": "test_kid",
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "n": "test_n",
                "e": "AQAB",
            }
        ]
    }

@pytest.fixture
def clerk_token():
    payload = {
        "sub": "user_123",
        "email": "test@example.com",
        "first_name": "Test",
        "last_name": "User",
        "iss": "https://clerk.example.com",
        "aud": "clerk_project_id",
        "exp": 9999999999
    }
    # Mocking jwt.decode to return this payload
    return "fake_token"

@pytest.mark.django_db
class TestClerkAuthentication:
    url = reverse('clerk_token_verify')

    @patch('users.auth.clerk.ClerkJWKSClient.get_jwks')
    @patch('jose.jwt.decode')
    @patch('jose.jwt.get_unverified_header')
    def test_verify_token_success(self, mock_header, mock_decode, mock_get_jwks, api_client, mock_jwks):
        mock_get_jwks.return_value = mock_jwks
        mock_header.return_value = {"kid": "test_kid"}
        mock_decode.return_value = {
            "sub": "clerk_123",
            "email": "clerk@example.com",
            "first_name": "Clerk",
            "last_name": "User",
            "iss": "https://clerk.example.com",
            "aud": "clerk_project_id"
        }

        response = api_client.post(
            self.url,
            HTTP_AUTHORIZATION='Bearer valid_token'
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert 'access' in response.data['data']
        
        # Verify user was created
        user = User.objects.get(clerk_id="clerk_123")
        assert user.email == "clerk@example.com"
        assert user.first_name == "Clerk"

    def test_missing_bearer_token(self, api_client):
        response = api_client.post(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch('users.auth.clerk.ClerkJWKSClient.get_jwks')
    @patch('jose.jwt.decode')
    @patch('jose.jwt.get_unverified_header')
    def test_invalid_signature(self, mock_header, mock_decode, mock_get_jwks, api_client, mock_jwks):
        mock_get_jwks.return_value = mock_jwks
        mock_header.return_value = {"kid": "test_kid"}
        mock_decode.side_effect = Exception("Invalid signature")

        response = api_client.post(
            self.url,
            HTTP_AUTHORIZATION='Bearer invalid_token'
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data['message'] == "Invalid authentication token."

    @patch('users.auth.clerk.ClerkJWKSClient.get_jwks')
    @patch('jose.jwt.decode')
    @patch('jose.jwt.get_unverified_header')
    def test_expired_token(self, mock_header, mock_decode, mock_get_jwks, api_client, mock_jwks):
        mock_get_jwks.return_value = mock_jwks
        mock_header.return_value = {"kid": "test_kid"}
        mock_decode.side_effect = jwt.ExpiredSignatureError()

        response = api_client.post(
            self.url,
            HTTP_AUTHORIZATION='Bearer expired_token'
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data['message'] == "Token has expired."
