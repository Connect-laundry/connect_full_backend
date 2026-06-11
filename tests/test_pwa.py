import json
import hashlib
from django.urls import reverse
from django.conf import settings
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from marketplace.models import PushDevice

User = get_user_model()


class PWATests(APITestCase):
    def setUp(self):
        # Create a platform admin user for testing API endpoints
        self.admin_user = User.objects.create_user(
            email='admin@example.com',
            phone='233555300002',
            password='StrongPass123!',
            first_name='Admin',
            last_name='User',
            role='ADMIN',
            is_staff=True,
            is_superuser=True
        )
        self.client.force_authenticate(user=self.admin_user)

    def test_manifest_serving(self):
        url = reverse('pwa_manifest')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.headers['Content-Type'], 'application/manifest+json')
        
        # Verify it is valid JSON and contains core configurations
        data = json.loads(response.content)
        self.assertEqual(data['name'], 'Connect Laundry Admin')
        self.assertEqual(data['short_name'], 'Connect Admin')
        self.assertEqual(data['start_url'], '/dashboard/')
        self.assertEqual(data['display'], 'standalone')

    def test_service_worker_serving(self):
        url = reverse('pwa_service_worker')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.headers['Content-Type'], 'application/javascript')
        
        # Verify the version is correctly injected via context
        content = response.content.decode('utf-8')
        expected_cache_name = f"connect-admin-cache-v{settings.PWA_VERSION}"
        self.assertIn(expected_cache_name, content)

    def test_offline_page_serving(self):
        url = reverse('pwa_offline')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('text/html', response.headers['Content-Type'])
        self.assertIn('You are offline', response.content.decode('utf-8'))

    def test_dashboard_redirect(self):
        url = reverse('dashboard_redirect')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response.url, '/admin/')

    def test_push_device_web_registration_unauthenticated(self):
        self.client.logout()
        url = reverse('admin_notifications_push_device')
        payload = {
            'endpoint': 'https://fcm.googleapis.com/fcm/send/fake-endpoint-token',
            'keys': {
                'p256dh': 'BIdn2JpX0b0J0gJ8_VlE-xG1-s2Rz6kU8eWd1Y4r5t-W-zLd6vGvLd6-rG9yYt2H-t_rWd3uX5r2',
                'auth': 'secret-auth-key'
            }
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_push_device_web_registration_flow(self):
        url = reverse('admin_notifications_push_device')
        endpoint = 'https://fcm.googleapis.com/fcm/send/fake-endpoint-token'
        payload = {
            'endpoint': endpoint,
            'keys': {
                'p256dh': 'BIdn2JpX0b0J0gJ8_VlE-xG1-s2Rz6kU8eWd1Y4r5t-W-zLd6vGvLd6-rG9yYt2H-t_rWd3uX5r2',
                'auth': 'secret-auth-key'
            }
        }
        
        # 1. Register device
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'success')
        
        # Verify db persistence
        expected_token = hashlib.sha256(endpoint.encode('utf-8')).hexdigest()
        device = PushDevice.objects.get(token=expected_token)
        self.assertEqual(device.user, self.admin_user)
        self.assertEqual(device.platform, PushDevice.Platform.WEB)
        self.assertEqual(device.web_endpoint, endpoint)
        self.assertEqual(device.web_p256dh, payload['keys']['p256dh'])
        self.assertEqual(device.web_auth, payload['keys']['auth'])
        self.assertTrue(device.is_active)

        # 2. Re-register (Update) same device
        payload['keys']['auth'] = 'updated-auth-key'
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        device.refresh_from_db()
        self.assertEqual(device.web_auth, 'updated-auth-key')

        # 3. Deactivate device
        response = self.client.delete(url, {'endpoint': endpoint}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        device.refresh_from_db()
        self.assertFalse(device.is_active)
