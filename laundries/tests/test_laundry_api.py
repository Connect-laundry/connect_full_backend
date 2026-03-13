from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from laundries.models.laundry import Laundry
from laundries.models.opening_hours import OpeningHours
import uuid

User = get_user_model()

class LaundryAPITests(APITestCase):
    def setUp(self):
        Laundry.objects.all().delete()
        self.user = User.objects.create_user(
            email='test@example.com',
            password='password123',
            phone='0123456789',
            role='CUSTOMER'
        )
        self.owner = User.objects.create_user(
            email='owner@example.com',
            password='password123',
            phone='0987654321',
            role='OWNER'
        )
        
        # Create featured laundry
        self.featured_laundry = Laundry.objects.create(
            name="Featured Laundry",
            description="Best laundry",
            address="123 Test St",
            latitude=5.6,
            longitude=-0.1,
            owner=self.owner,
            is_featured=True,
            is_active=True,
            status=Laundry.ApprovalStatus.APPROVED
        )
        
        # Create opening hours
        OpeningHours.objects.create(
            laundry=self.featured_laundry,
            day=1, # Monday
            opening_time="08:00:00",
            closing_time="18:00:00"
        )

        # Create non-featured laundry
        self.regular_laundry = Laundry.objects.create(
            name="Regular Laundry",
            description="Normal laundry",
            address="456 Test St",
            latitude=5.7,
            longitude=-0.2,
            owner=self.owner,
            is_featured=False,
            is_active=True,
            status=Laundry.ApprovalStatus.APPROVED
        )

    def test_featured_laundries_endpoint(self):
        """Test the dedicated featured endpoint (nested under /laundries/laundries/)."""
        url = reverse('laundry-featured')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check if it only returns the featured laundry
        data = response.data
        results = data.get('data', data)
        if isinstance(results, dict) and 'results' in results:
             results = results['results']

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Featured Laundry")

    def test_featured_laundries_top_level_endpoint(self):
        """Test the direct featured endpoint (api/v1/laundries/featured/)."""
        url = reverse('laundry-featured-top')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        results = data.get('data', data)
        if isinstance(results, dict) and 'results' in results:
             results = results['results']

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Featured Laundry")

    def test_laundry_list_featured_filter(self):
        """Test the list endpoint with ?is_featured=true filter."""
        url = reverse('laundry-list')
        response = self.client.get(url, {'is_featured': 'true'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # StandardResultsSetPagination is used, results are in 'results' or enveloped
        data = response.data
        results = data.get('data', data)
        if isinstance(results, dict) and 'results' in results:
             results = results['results']

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Featured Laundry")
