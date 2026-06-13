import pytest
import csv
import io
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile

from laundries.models.laundry import Laundry, OwnerAuditLog
from laundries.models.pricing import (
    LaundryPricingItem, PricingCatalogVersion, ScheduledPriceChange, DeliveryZonePricing
)
from laundries.models.opening_hours import OpeningHours, HolidayOverride
from users.models import User
from laundries.tasks import apply_scheduled_pricing_changes
from ordering.services.finance_service import FinanceService

def _owner(email='owner-mod@example.com', phone='233500090001'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )

def _customer(email='cust-mod@example.com', phone='233500090009'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.CUSTOMER
    )

def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c

def _laundry(owner):
    return Laundry.objects.create(
        owner=owner, name='Modern Laundry', address='123 Osu, Accra', city='Accra',
        latitude=Decimal('5.603700'), longitude=Decimal('-0.187000'), phone_number='0240000020',
        service_radius_km=Decimal('5.0'),
        service_area_polygon={
            "type": "Polygon",
            "coordinates": [[
                [-0.200000, 5.590000],
                [-0.170000, 5.590000],
                [-0.170000, 5.620000],
                [-0.200000, 5.620000],
                [-0.200000, 5.590000]
            ]]
        }
    )

@pytest.mark.django_db
class TestModernizationFeatures:

    def test_default_categories(self):
        owner = _owner()
        _laundry(owner)
        client = _client(owner)
        url = reverse('dashboard-pricing-items-default-categories')
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data.get('data', resp.data)
        assert "Shirts" in data
        assert "Bedding" in data

    def test_template_download(self):
        owner = _owner()
        _laundry(owner)
        client = _client(owner)
        url = reverse('dashboard-pricing-items-download-template')
        resp = client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp['Content-Type'] == 'text/csv'
        assert 'attachment' in resp['Content-Disposition']

    def test_bulk_import_csv(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)
        
        # 1. Create a couple of initial items
        LaundryPricingItem.objects.create(laundry=laundry, item_name='Jeans', unit_price=10.00, category='Trousers')
        LaundryPricingItem.objects.create(laundry=laundry, item_name='Shirt', unit_price=8.00, category='Shirts')

        # Create CSV payload
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(['item_name', 'category', 'unit_price', 'is_active', 'display_order'])
        writer.writerow(['Shirt', 'Shirts', '12.00', 'True', '0'])  # update price
        writer.writerow(['Duvet', 'Bedding', '30.00', 'True', '2'])  # new item
        
        csv_file = SimpleUploadedFile(
            'pricing.csv',
            csv_buffer.getvalue().encode('utf-8'),
            content_type='text/csv'
        )

        url = reverse('dashboard-pricing-items-import-bulk')
        
        # Test Import with overwrite = False (Update mode)
        resp = client.post(url, {'file': csv_file, 'overwrite': 'false'}, format='multipart')
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert LaundryPricingItem.objects.filter(laundry=laundry).count() == 3
        # Shirt should be updated
        assert LaundryPricingItem.objects.get(laundry=laundry, item_name='Shirt').unit_price == Decimal('12.00')
        # Jeans should still exist
        assert LaundryPricingItem.objects.filter(laundry=laundry, item_name='Jeans').exists()

        # Test Import with overwrite = True (Overwrite mode)
        csv_buffer_overwrite = io.StringIO()
        writer_ow = csv.writer(csv_buffer_overwrite)
        writer_ow.writerow(['item_name', 'category', 'unit_price', 'is_active', 'display_order'])
        writer_ow.writerow(['Suit', 'Suits', '50.00', 'True', '0'])
        
        csv_file_ow = SimpleUploadedFile(
            'pricing_ow.csv',
            csv_buffer_overwrite.getvalue().encode('utf-8'),
            content_type='text/csv'
        )
        
        resp_ow = client.post(url, {'file': csv_file_ow, 'overwrite': 'true'}, format='multipart')
        assert resp_ow.status_code == status.HTTP_200_OK, resp_ow.data
        # Old items should be wiped out
        assert LaundryPricingItem.objects.filter(laundry=laundry).count() == 1
        assert LaundryPricingItem.objects.get(laundry=laundry).item_name == 'Suit'

    def test_pricing_version_rollback(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)

        # 1. Setup initial items
        LaundryPricingItem.objects.create(laundry=laundry, item_name='ItemA', unit_price=5.00)
        LaundryPricingItem.objects.create(laundry=laundry, item_name='ItemB', unit_price=10.00)

        # 2. Trigger version snapshot via POST /api/v1/laundries/dashboard/pricing-items/versions/
        version_url = reverse('dashboard-pricing-versions-list')
        resp_v = client.post(version_url, {})
        assert resp_v.status_code == status.HTTP_201_CREATED, resp_v.data
        version_id = resp_v.data['id']

        # 3. Change items
        LaundryPricingItem.objects.filter(laundry=laundry).delete()
        LaundryPricingItem.objects.create(laundry=laundry, item_name='ItemC', unit_price=15.00)

        # 4. Rollback
        rollback_url = reverse('dashboard-pricing-versions-rollback', kwargs={'pk': version_id})
        resp_r = client.post(rollback_url, {})
        assert resp_r.status_code == status.HTTP_200_OK, resp_r.data

        # Verify rollback restored the original items
        items = LaundryPricingItem.objects.filter(laundry=laundry).order_by('item_name')
        assert items.count() == 2
        assert items[0].item_name == 'ItemA'
        assert items[1].item_name == 'ItemB'

    def test_scheduled_price_change(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)

        LaundryPricingItem.objects.create(laundry=laundry, item_name='Towel', unit_price=4.00)

        # Create scheduled change
        change_url = reverse('dashboard-scheduled-prices-list')
        effective_time = timezone.now() + timezone.timedelta(minutes=5)
        payload = {
            'effective_at': effective_time.isoformat(),
            'pricing_data': [
                {'item_name': 'Towel', 'unit_price': '6.50', 'category': 'Household', 'is_active': True, 'display_order': 1},
                {'item_name': 'Blanket', 'unit_price': '22.00', 'category': 'Bedding', 'is_active': True, 'display_order': 2}
            ]
        }
        resp = client.post(change_url, payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        
        # Verify not applied yet
        assert LaundryPricingItem.objects.get(laundry=laundry, item_name='Towel').unit_price == Decimal('4.00')

        # Run task before effective time -> should not apply
        apply_scheduled_pricing_changes()
        assert LaundryPricingItem.objects.get(laundry=laundry, item_name='Towel').unit_price == Decimal('4.00')

        # Shift effective time back so it's in the past and run task
        change_obj = ScheduledPriceChange.objects.first()
        change_obj.effective_at = timezone.now() - timezone.timedelta(minutes=1)
        change_obj.save()

        apply_scheduled_pricing_changes()
        
        # Verify applied
        assert LaundryPricingItem.objects.get(laundry=laundry, item_name='Towel').unit_price == Decimal('6.50')
        assert LaundryPricingItem.objects.get(laundry=laundry, item_name='Blanket').unit_price == Decimal('22.00')
        
        change_obj.refresh_from_db()
        assert change_obj.is_applied is True

    def test_delivery_zone_pricing(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)

        # Create delivery zones
        zone_url = reverse('dashboard-delivery-zones-list')
        client.post(zone_url, {'min_distance_km': '0.00', 'max_distance_km': '2.00', 'delivery_fee': '5.00', 'pickup_fee': '1.00'}, format='json')
        client.post(zone_url, {'min_distance_km': '2.01', 'max_distance_km': '5.00', 'delivery_fee': '12.00', 'pickup_fee': '3.00'}, format='json')

        class DummyOrder:
            def __init__(self, pickup_lat, pickup_lng, laundry):
                self.pickup_lat = pickup_lat
                self.pickup_lng = pickup_lng
                self.laundry = laundry

        # Test zone 1 matching (1km away)
        # Laundry is at 5.603700, -0.187000
        # 1km away coordinates approx: 5.603700, -0.178000
        order_near = DummyOrder(Decimal('5.603700'), Decimal('-0.178000'), laundry)
        assert FinanceService.calculate_delivery_fee(order_near) == Decimal('5.00')
        assert FinanceService.calculate_pickup_fee(order_near) == Decimal('1.00')

        # Test zone 2 matching (4km away)
        # 4km away coordinates approx: 5.603700, -0.151000
        order_far = DummyOrder(Decimal('5.603700'), Decimal('-0.151000'), laundry)
        assert FinanceService.calculate_delivery_fee(order_far) == Decimal('12.00')
        assert FinanceService.calculate_pickup_fee(order_far) == Decimal('3.00')

    def test_holiday_override_open_now(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)

        # Set standard opening hours for Monday
        oh = OpeningHours.objects.create(
            laundry=laundry, day=1, opening_time='08:00:00', closing_time='20:00:00', is_closed=False
        )

        from laundries.views.laundry import get_open_laundry_ids
        
        # Test Monday at 12:00 -> should be open
        now_open = timezone.now().replace(year=2026, month=6, day=15, hour=12, minute=0) # June 15 2026 is Monday
        assert laundry.id in get_open_laundry_ids(now_open)

        # Create holiday override for that date closing the shop
        HolidayOverride.objects.create(
            laundry=laundry, date=now_open.date(), is_closed=True, note="Christmas Closed"
        )
        
        # Monday at 12:00 is now closed due to override
        assert laundry.id not in get_open_laundry_ids(now_open)

    def test_order_geofence_restrictions(self):
        def local_build_booking_catalog(prefix='Geofence'):
            from laundries.models.category import Category
            from ordering.models import LaunderableItem
            owner = User.objects.create_user(
                email=f'{prefix.lower()}-owner@example.com',
                phone='233555901991',
                password='StrongPass123!',
                role=User.Role.OWNER,
            )
            customer = User.objects.create_user(
                email=f'{prefix.lower()}-customer@example.com',
                phone='233555901992',
                password='StrongPass123!',
            )
            service_type = Category.objects.create(
                name=f'{prefix} Wash',
                type=Category.CategoryType.SERVICE_TYPE,
            )
            other_service_type = Category.objects.create(
                name=f'{prefix} Iron',
                type=Category.CategoryType.SERVICE_TYPE,
            )
            item_category = Category.objects.create(
                name=f'{prefix} Shirts',
                type=Category.CategoryType.ITEM_CATEGORY,
            )
            item = LaunderableItem.objects.create(
                name=f'{prefix} Shirt',
                item_category=item_category,
            )
            laundry = Laundry.objects.create(
                name=f'{prefix} Laundry',
                description='Booking test laundry',
                address='Accra',
                city='Accra',
                latitude='5.603700',
                longitude='-0.187000',
                phone_number='0240000002',
                owner=owner,
                status=Laundry.ApprovalStatus.APPROVED,
                is_active=True,
            )
            from laundries.models.service import LaundryService
            LaundryService.objects.create(
                laundry=laundry,
                item=item,
                service_type=service_type,
                price='25.00',
                is_available=True,
            )
            return customer, laundry, item, service_type, other_service_type

        def local_booking_payload(laundry, item, service_type):
            return {
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timezone.timedelta(days=1)).isoformat(),
                'pickup_address': 'Pickup Address',
                'pickup_lat': '5.6037000',
                'pickup_lng': '-0.1870000',
                'delivery_address': 'Delivery Address',
                'delivery_lat': '5.6037000',
                'delivery_lng': '-0.1870000',
                'items': [
                    {
                        'item': str(item.id),
                        'service_type': str(service_type.id),
                        'quantity': 2,
                    }
                ],
                'special_instructions': 'Handle carefully.',
                'payment_method': 'CARD',
            }

        customer, laundry, item, service_type, _ = local_build_booking_catalog('Geofence')
        
        # We need to set the laundry's service radius and service area polygon in the db
        laundry.service_radius_km = Decimal('5.0')
        laundry.service_area_polygon = {
            "type": "Polygon",
            "coordinates": [[
                [-0.200000, 5.590000],
                [-0.170000, 5.590000],
                [-0.170000, 5.620000],
                [-0.200000, 5.620000],
                [-0.200000, 5.590000]
            ]]
        }
        laundry.save()

        client_c = _client(customer)

        from ordering.serializers.order import OrderCreateSerializer

        # Coordinates inside the custom polygon (Osu, Accra)
        inside_coords = local_booking_payload(laundry, item, service_type)
        inside_coords['pickup_lat'] = '5.600000'
        inside_coords['pickup_lng'] = '-0.180000'
        inside_coords['delivery_lat'] = '5.600000'
        inside_coords['delivery_lng'] = '-0.180000'
        
        # Coordinates outside the polygon and radius (e.g. Tema, Ghana)
        outside_coords = inside_coords.copy()
        outside_coords['pickup_lat'] = '5.700000'
        outside_coords['pickup_lng'] = '-0.010000'

        # Mock view/serializer context
        serializer_context = {'request': client_c.get('/').request}
        
        # Outside coords should raise ValidationError
        serializer_out = OrderCreateSerializer(data=outside_coords, context=serializer_context)
        with pytest.raises(Exception) as excinfo:
            serializer_out.is_valid(raise_exception=True)
        assert "service coverage area" in str(excinfo.value)


