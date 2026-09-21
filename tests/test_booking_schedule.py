from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from ordering.models import BookingSlot
from test_booking_create import _auth_client, _build_booking_catalog, _booking_payload


@pytest.mark.django_db
class TestPickupSchedule:
    def test_filters_selected_day_expired_full_and_unavailable_slots(self):
        customer, laundry, *_ = _build_booking_catalog('Schedule')
        start = (timezone.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        available = BookingSlot.objects.create(laundry=laundry, start_time=start, end_time=start + timedelta(hours=1))
        for offset, kwargs in [(2, {'current_bookings': 5}), (4, {'is_available': False}), (24, {})]:
            slot_start = start + timedelta(hours=offset)
            BookingSlot.objects.create(laundry=laundry, start_time=slot_start, end_time=slot_start + timedelta(hours=1), **kwargs)
        BookingSlot.objects.create(laundry=laundry, start_time=timezone.now() - timedelta(hours=2), end_time=timezone.now() - timedelta(hours=1))
        response = _auth_client(customer).get(reverse('booking-schedule'), {'laundry_id': str(laundry.id), 'date': start.date().isoformat()})
        assert response.status_code == 200
        assert [slot['id'] for slot in response.data['data']] == [str(available.id)]

    @pytest.mark.parametrize('query', [{'laundry_id': 'invalid'}, {'date': 'invalid'}, {'date': '2030-02-31'}])
    def test_invalid_filters_are_validation_errors(self, query):
        customer, laundry, *_ = _build_booking_catalog('InvalidSchedule')
        params = {'laundry_id': str(laundry.id), **query}
        assert _auth_client(customer).get(reverse('booking-schedule'), params).status_code == 400

    def test_no_availability_returns_empty_collection(self):
        customer, laundry, *_ = _build_booking_catalog('EmptySchedule')
        response = _auth_client(customer).get(reverse('booking-schedule'), {'laundry_id': str(laundry.id)})
        assert response.status_code == 200
        assert response.data['data'] == []

    def test_schedule_requires_authentication(self):
        assert APIClient().get(reverse('booking-schedule')).status_code in (401, 403)

    def test_selected_time_and_location_survive_order_creation(self):
        customer, laundry, item, service, _ = _build_booking_catalog('ScheduledOrder')
        payload = _booking_payload(laundry, item, service)
        payload['payment_method'] = 'CASH'
        start = timezone.now() + timedelta(days=1)
        BookingSlot.objects.create(laundry=laundry, start_time=start, end_time=start + timedelta(hours=1))
        payload['pickup_date'] = start.isoformat()
        # Test settings use in-memory DB and eager tasks; no real customer payment.
        response = _auth_client(customer).post(reverse('booking-create'), payload, format='json')
        assert response.status_code == 201, response.data
        from ordering.models import Order
        order = Order.objects.get(user=customer)
        assert order.pickup_date == start
        assert order.pickup_address == payload['pickup_address']
        assert float(order.pickup_lat) == float(payload['pickup_lat'])
