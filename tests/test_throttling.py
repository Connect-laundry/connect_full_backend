# pyre-ignore[missing-module]
import pytest
# pyre-ignore[missing-module]
from django.urls import reverse
# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from django.core.cache import cache

@pytest.mark.django_db
class TestThrottling:
    def setup_method(self):
        cache.clear()

    def test_auth_throttle(self, client):
        url = reverse('register')
        # Rates is 5/minute in our test settings (from os.getenv logic)
        for _ in range(5):
            response = client.post(url, data={})
            # Auth throttle triggered after n requests. 
            # Note: client is anon by default
        
        response = client.post(url, data={})
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response.data['status'] == 'error'
        assert 'Too many requests' in response.data['message']

    def test_feedback_throttle(self, auth_client):
        url = reverse('feedback')
        # Rate is 3/hour
        for _ in range(3):
            response = auth_client.post(url, data={"subject": "test", "message": "test"})
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS
            
        response = auth_client.post(url, data={"subject": "test", "message": "test"})
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
