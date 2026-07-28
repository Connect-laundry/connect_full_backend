"""Price + review annotations exposed on the laundry list endpoint.

The discovery cards used to render "GHS 0.00" because ``avgPrice`` was
serialized but never annotated. These tests lock in the annotations and, just
as importantly, the ``distinct=True`` counts — joining reviews, orders and
services in one ``annotate()`` fans the rows out and silently multiplies any
plain ``Count``.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.review import Review
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, Order

User = get_user_model()


class LaundryPriceSignalTests(APITestCase):
    def setUp(self):
        Laundry.objects.all().delete()
        self.owner = User.objects.create_user(
            email='priceowner@example.com', phone='233700100001',
            password='pw', role='OWNER')
        self.customer = User.objects.create_user(
            email='pricecustomer@example.com', phone='233700100002', password='pw')

        self.laundry = Laundry.objects.create(
            name='Priced Laundry', description='d', address='Accra',
            latitude=5.6, longitude=-0.1, phone_number='0240000010',
            owner=self.owner, is_active=True,
            status=Laundry.ApprovalStatus.APPROVED,
        )

        self.service_type = Category.objects.create(
            name='Price Wash', type=Category.CategoryType.SERVICE_TYPE)
        self.shirt = LaunderableItem.objects.create(name='Price Shirt')
        self.duvet = LaunderableItem.objects.create(name='Price Duvet')

    def _service(self, item, price, available=True):
        return LaundryService.objects.create(
            laundry=self.laundry, item=item, service_type=self.service_type,
            price=Decimal(price), is_available=available,
        )

    def _list_payload(self):
        self.client.force_authenticate(user=self.customer)
        response = self.client.get(reverse('laundry-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('data', response.data)
        if isinstance(results, dict):
            results = results.get('results', [])
        return next(row for row in results if str(row['id']) == str(self.laundry.id))

    def test_avg_and_min_price_are_annotated(self):
        self._service(self.shirt, '6.00')
        self._service(self.duvet, '20.00')

        row = self._list_payload()

        self.assertEqual(row['avgPrice'], 13.0)
        self.assertEqual(row['minServicePrice'], 6.0)

    def test_unavailable_services_are_excluded_from_price_signals(self):
        self._service(self.shirt, '6.00')
        self._service(self.duvet, '20.00', available=False)

        row = self._list_payload()

        self.assertEqual(row['avgPrice'], 6.0)
        self.assertEqual(row['minServicePrice'], 6.0)

    def test_price_signals_are_null_when_no_services_exist(self):
        row = self._list_payload()

        # Null, never 0 — the client falls through to its next price signal.
        self.assertIsNone(row['avgPrice'])
        self.assertIsNone(row['minServicePrice'])

    def test_review_count_is_not_inflated_by_the_service_join(self):
        """Regression: without distinct=True this returns reviews x services."""
        for price in ('6.00', '12.00', '20.00'):
            LaundryService.objects.create(
                laundry=self.laundry,
                item=LaunderableItem.objects.create(name=f'Item {price}'),
                service_type=self.service_type,
                price=Decimal(price), is_available=True,
            )
        Review.objects.create(laundry=self.laundry, user=self.customer, rating=4)

        row = self._list_payload()

        self.assertEqual(row['reviewsCount'], 1)
        self.assertEqual(row['rating'], 4.0)

    def test_unrated_laundry_reports_zero_reviews_and_null_rating(self):
        self._service(self.shirt, '6.00')

        row = self._list_payload()

        self.assertEqual(row['reviewsCount'], 0)
        self.assertIsNone(row['rating'])


class LaundryDiscoveryQueryTests(APITestCase):
    """Query contract behind the Home "See All" sections."""

    def setUp(self):
        Laundry.objects.all().delete()
        self.owner = User.objects.create_user(
            email='discoveryowner@example.com', phone='233700110001',
            password='pw', role='OWNER')
        self.customer = User.objects.create_user(
            email='discoverycustomer@example.com', phone='233700110002', password='pw')

        self.featured = self._laundry('Featured One', is_featured=True)
        self.regular = self._laundry('Regular One', is_featured=False)

        self.service_type = Category.objects.create(
            name='Discovery Wash', type=Category.CategoryType.SERVICE_TYPE)

    def _laundry(self, name, *, is_featured):
        return Laundry.objects.create(
            name=name, description='d', address='Accra',
            latitude=5.6, longitude=-0.1, phone_number='0240000011',
            owner=self.owner, is_active=True, is_featured=is_featured,
            status=Laundry.ApprovalStatus.APPROVED,
        )

    def _names(self, params):
        self.client.force_authenticate(user=self.customer)
        response = self.client.get(reverse('laundry-list'), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('data', response.data)
        if isinstance(results, dict):
            results = results.get('results', [])
        return [row['name'] for row in results]

    def test_is_featured_true_returns_only_featured(self):
        self.assertEqual(self._names({'is_featured': 'true'}), ['Featured One'])

    def test_featured_alias_is_accepted(self):
        self.assertEqual(self._names({'featured': 'true'}), ['Featured One'])

    def test_unfiltered_list_returns_everything(self):
        self.assertCountEqual(
            self._names({}), ['Featured One', 'Regular One'])

    def test_nearby_without_postgis_degrades_instead_of_erroring(self):
        # PostGIS is off in the test settings; the endpoint must still answer.
        names = self._names({'nearby': 'true', 'lat': '5.6', 'lng': '-0.1'})
        self.assertCountEqual(names, ['Featured One', 'Regular One'])

    def test_cheapest_orders_by_average_price_nulls_last(self):
        shirt = LaunderableItem.objects.create(name='Discovery Shirt')
        duvet = LaunderableItem.objects.create(name='Discovery Duvet')
        LaundryService.objects.create(
            laundry=self.regular, item=shirt, service_type=self.service_type,
            price=Decimal('5.00'), is_available=True)
        LaundryService.objects.create(
            laundry=self.featured, item=duvet, service_type=self.service_type,
            price=Decimal('50.00'), is_available=True)
        unpriced = self._laundry('Unpriced One', is_featured=False)

        names = self._names({'cheapest': 'true'})

        self.assertEqual(names[0], 'Regular One')
        self.assertEqual(names[1], 'Featured One')
        # A laundry with no services must not masquerade as the cheapest.
        self.assertEqual(names[-1], unpriced.name)
