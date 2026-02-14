from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from marketplace.models import Notification
from ordering.models import Order
from laundries.models.laundry import Laundry
from django.utils import timezone
from unittest.mock import patch

User = get_user_model()

class NotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="password", role='CUSTOMER')
        self.owner = User.objects.create_user(email="owner@example.com", password="password", role='OWNER')
        self.laundry = Laundry.objects.create(name="Test Laundry", owner=self.owner, address="Test Address")
        self.client.force_authenticate(user=self.user)

    @patch('marketplace.tasks.create_notification.delay')
    def test_order_creation_triggers_owner_notification(self, mock_task):
        """Creating an order should trigger a notification to the laundry owner."""
        Order.objects.create(
            user=self.user,
            laundry=self.laundry,
            total_amount=100.00,
            pickup_date=timezone.now(),
            address="Test Address"
        )
        mock_task.assert_called()

    def test_mark_as_read(self):
        notification = Notification.objects.create(
            user=self.user,
            title="Test",
            body="Test body"
        )
        url = reverse('notification-mark-read', kwargs={'pk': notification.id})
        response = self.client.patch(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_read(self):
        Notification.objects.create(user=self.user, title="1", body="1")
        Notification.objects.create(user=self.user, title="2", body="2")
        url = reverse('notification-mark-all-read')
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 0)
