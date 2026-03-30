import uuid
from decimal import Decimal
# pyre-ignore[missing-module]
from django.urls import reverse
# pyre-ignore[missing-module]
from rest_framework import status
# pyre-ignore[missing-module]
from rest_framework.test import APITestCase
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model
# pyre-ignore[missing-module]
from laundries.models.laundry import Laundry
# pyre-ignore[missing-module]
from laundries.models.opening_hours import OpeningHours

User = get_user_model()


class OwnerLaundryAPITests(APITestCase):
    def setUp(self):
        # Create an owner user
        self.owner = User.objects.create_user(
            email='testowner@example.com',
            phone='+1234567890',
            password='Password123!',  # nosec B106
            role='OWNER',
            is_verified=True
        )
        # Create a customer user to test unauthorized access
        self.customer = User.objects.create_user(
            email='customer@example.com',
            phone='+0987654321',
            password='Password123!',  # nosec B106
            role='CUSTOMER',
            is_verified=True
        )

    def create_test_laundry(
            self,
            name,
            status=Laundry.ApprovalStatus.PENDING,
            is_active=False):
        return Laundry.objects.create(
            owner=self.owner,
            name=name,
            address="123 Test St",
            city="Accra",
            latitude=5.6037,
            longitude=-0.1870,
            phone_number="0551234567",
            status=status,
            is_active=is_active
        )

    def test_create_storefront(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse('owner-laundry-list')

        data = {
            "name": "Test Fresh Laundry",
            "address": "123 Main St",
            "city": "Accra",
            "latitude": 5.6037,
            "longitude": -0.1870,
            "phone_number": "0551234567",
            "delivery_fee": "15.00",
            "pickup_fee": "10.00",
            "min_order": "50.00"
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'success')
        self.assertEqual(response.data['data']['name'], 'Test Fresh Laundry')

        # Verify it defaults to Pending
        laundry = Laundry.objects.get(owner=self.owner)
        self.assertEqual(laundry.status, Laundry.ApprovalStatus.PENDING)
        self.assertFalse(laundry.is_active)

    def test_customer_cannot_create_storefront(self):
        self.client.force_authenticate(user=self.customer)
        url = reverse('owner-laundry-list')

        data = {"name": "Test Fresh Laundry", "address": "123 Main St"}
        response = self.client.post(url, data, format='json')

        # Should be forbidden for non-owners
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_storefront(self):
        self.client.force_authenticate(user=self.owner)
        # Create initial laundry
        laundry = self.create_test_laundry(name="Old Name")

        url = reverse('owner-laundry-detail', kwargs={'pk': laundry.id})
        data = {"name": "New Awesome Name", "delivery_fee": "25.00"}

        response = self.client.patch(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        laundry.refresh_from_db()
        self.assertEqual(laundry.name, "New Awesome Name")
        self.assertEqual(laundry.delivery_fee, Decimal('25.00'))

    def test_opening_hours_crud(self):
        self.client.force_authenticate(user=self.owner)
        laundry = self.create_test_laundry(name="Hours Test")
        url = reverse('owner-laundry-hours', kwargs={'pk': laundry.id})

        # Set schedule (PUT replaces entire schedule)
        schedule_data = [{"day": 1,
                          "opening_time": "08:00:00",
                          "closing_time": "18:00:00",
                          "is_closed": False},
                         {"day": 2,
                          "opening_time": "08:00:00",
                          "closing_time": "18:00:00",
                          "is_closed": False},
                         {"day": 7,
                          "opening_time": "00:00:00",
                          "closing_time": "00:00:00",
                          "is_closed": True}]

        response = self.client.put(url, schedule_data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify in database
        hours_count = OpeningHours.objects.filter(laundry=laundry).count()
        self.assertEqual(hours_count, 3)
        sunday = OpeningHours.objects.get(laundry=laundry, day=7)
        self.assertTrue(sunday.is_closed)

    def test_toggle_store_status(self):
        self.client.force_authenticate(user=self.owner)
        # Laundry must be APPROVED by admin to be toggled
        laundry = self.create_test_laundry(
            name="Toggle Test",
            status=Laundry.ApprovalStatus.APPROVED,
            is_active=False
        )
        url = reverse('owner-laundry-toggle', kwargs={'pk': laundry.id})

        # First toggle: should open it
        response1 = self.client.patch(url)
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertTrue(response1.data['data']['is_active'])

        laundry.refresh_from_db()
        self.assertTrue(laundry.is_active)

        # Second toggle: should close it
        response2 = self.client.patch(
            url, {"reason": "Going on vacation"}, format='json')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        laundry.refresh_from_db()
        self.assertFalse(laundry.is_active)
        self.assertEqual(laundry.deactivation_reason, "Going on vacation")

    def test_toggle_pending_store_fails(self):
        self.client.force_authenticate(user=self.owner)
        # Pending stores cannot be "Opened" until admin approves
        laundry = self.create_test_laundry(
            name="Pending Test",
            status=Laundry.ApprovalStatus.PENDING,
            is_active=False
        )
        url = reverse('owner-laundry-toggle', kwargs={'pk': laundry.id})

        response = self.client.patch(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Only approved laundries can be toggled",
            response.data['message'])

    def test_get_owner_reviews(self):
        self.client.force_authenticate(user=self.owner)
        laundry = self.create_test_laundry(name="Review Test")

        # Mocking reviews
        # pyre-ignore[missing-module]
        from laundries.models.review import Review
        Review.objects.create(
            user=self.customer,
            laundry=laundry,
            rating=5,
            comment="Great service!")
        Review.objects.create(
            user=self.customer,
            laundry=laundry,
            rating=4,
            comment="Good.")

        url = reverse('owner-laundry-reviews', kwargs={'pk': laundry.id})

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(
            response.data['results'][0]['rating'],
            4)  # Order by -created_at usually
