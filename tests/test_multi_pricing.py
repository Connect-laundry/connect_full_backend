from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from users.models import User
from laundries.models.laundry import Laundry
from laundries.models.category import Category
from marketplace.models import LaunderableItem
from laundries.models.service import LaundryService

class MultiPricingTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@test.com", password="password123", role="OWNER"
        )
        self.client.force_authenticate(user=self.owner)
        self.category = Category.objects.create(name="Wash", type="L")
        self.item = LaunderableItem.objects.create(name="Shirt")
        self.laundry = Laundry.objects.create(
            owner=self.owner, name="Test Laundry", 
            phone_number="0241234567", latitude=5.6, longitude=-0.1
        )

    def test_enable_per_item_requires_services(self):
        """
        Test that enabling PER_ITEM pricing fails if no services are defined.
        """
        url = reverse('laundry-detail', kwargs={'pk': self.laundry.id})
        data = {"pricing_methods": ["PER_ITEM"]}
        response = self.client.patch(url, data, format='json')
        
        # Should fail because no services exist for this laundry
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("At least one item is required", str(response.data))

    def test_enable_per_item_success_with_services(self):
        """
        Test that enabling PER_ITEM pricing succeeds if services exist.
        """
        LaundryService.objects.create(
            laundry=self.laundry, item=self.item, 
            service_type=self.category, price=10.0
        )
        url = reverse('laundry-detail', kwargs={'pk': self.laundry.id})
        data = {"pricing_methods": ["PER_ITEM"]}
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("PER_ITEM", response.data['pricing_methods'])
