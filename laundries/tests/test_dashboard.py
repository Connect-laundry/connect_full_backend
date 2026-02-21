# pyre-ignore[missing-module]
from django.urls import reverse
# pyre-ignore[missing-module]
from rest_framework.test import APITestCase
# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model
# pyre-ignore[missing-module]
from ordering.models import Order
# pyre-ignore[missing-module]
from laundries.models.laundry import Laundry
# pyre-ignore[missing-module]
from django.utils import timezone

User = get_user_model()

class DashboardTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="password", role='OWNER')
        self.customer = User.objects.create_user(email="cust@example.com", password="password", role='CUSTOMER')
        self.laundry = Laundry.objects.create(name="Store 1", owner=self.owner, address="Addr")
        
        # Create some orders
        Order.objects.create(user=self.customer, laundry=self.laundry, status='PENDING', pickup_date=timezone.now(), total_amount=50.0)
        Order.objects.create(user=self.customer, laundry=self.laundry, status='DELIVERED', pickup_date=timezone.now(), total_amount=150.0)

    def test_owner_can_access_stats(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse('dashboard-stats')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['pending_count'], 1)
        self.assertEqual(response.data['data']['delivered_count'], 1)

    def test_customer_cannot_access_stats(self):
        self.client.force_authenticate(user=self.customer)
        url = reverse('dashboard-stats')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_earnings_calculation(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse('dashboard-earnings')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # 150.0 is delivered, 50.0 is pending (not in revenue)
        self.assertEqual(float(response.data['data']['total_revenue']), 150.0)
