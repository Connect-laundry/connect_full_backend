"""Vacation mode: setting it safely, and seeing it in the admin.

A laundry with vacation mode on shows Closed in the app all day and cannot be
booked, whatever its hours say. An early owner's laundry sat like that with
nothing in the admin explaining why, and the endpoint could only flip the
flag, so a double click or a retry left it in the opposite state.
"""
from datetime import time

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from laundries.admin import LaundryAdmin
from laundries.models.laundry import Laundry
from laundries.models.opening_hours import OpeningHours
from users.models import User

URL = '/api/v1/laundries/dashboard/my-laundry/toggle-vacation/'


@pytest.fixture
def owner(db):
    return User.objects.create_user(
        email='vac-owner@example.com', phone='233555960001',
        password='StrongPass123!', role=User.Role.OWNER)


@pytest.fixture
def laundry(owner):
    laundry = Laundry.objects.create(
        name='Vacation Laundry', description='d', address='Ayeduase', latitude=6.67,
        longitude=-1.56, phone_number='0240002001', owner=owner,
        status=Laundry.ApprovalStatus.APPROVED, is_active=True)
    for day in range(1, 8):
        # Ticked overnight flag on a same-day shift, like the real laundry.
        OpeningHours.objects.create(
            laundry=laundry, day=day, opening_time=time(8, 30),
            closing_time=time(22), is_overnight=True)
    return laundry


def _post(owner, body=None):
    client = APIClient()
    client.force_authenticate(user=owner)
    return client.post(URL, body, format='json') if body is not None else client.post(URL)


@pytest.mark.django_db
class TestSetVacationMode:
    def test_explicit_value_is_idempotent(self, owner, laundry):
        for _ in range(2):
            response = _post(owner, {'vacation_mode': False})
            assert response.status_code == 200
        laundry.refresh_from_db()
        assert laundry.vacation_mode is False

        for _ in range(2):
            _post(owner, {'vacation_mode': True})
        laundry.refresh_from_db()
        assert laundry.vacation_mode is True

    def test_string_values_are_accepted(self, owner, laundry):
        _post(owner, {'vacation_mode': 'true'})
        laundry.refresh_from_db()
        assert laundry.vacation_mode is True

    def test_empty_body_still_toggles_for_older_clients(self, owner, laundry):
        _post(owner)
        laundry.refresh_from_db()
        assert laundry.vacation_mode is True
        _post(owner)
        laundry.refresh_from_db()
        assert laundry.vacation_mode is False

    def test_invalid_value_is_rejected(self, owner, laundry):
        response = _post(owner, {'vacation_mode': 'maybe'})
        assert response.status_code == 400
        laundry.refresh_from_db()
        assert laundry.vacation_mode is False


@pytest.mark.django_db
class TestAdminShowsCustomerAvailability:
    def _admin(self):
        from django.contrib.admin.sites import site
        return LaundryAdmin(Laundry, site)

    def test_vacation_mode_is_named_as_the_reason(self, laundry):
        laundry.vacation_mode = True
        laundry.save()
        text = str(self._admin().customer_availability(laundry))
        assert 'vacation mode is ON' in text
        assert 'cannot book' in text

    def test_open_laundry_is_reported_open_or_with_next_opening(self, laundry):
        text = str(self._admin().customer_availability(laundry))
        assert 'Open now' in text or 'Opens' in text

    def test_same_day_shift_is_not_labelled_overnight(self, laundry):
        text = str(self._admin().hours_summary(laundry))
        assert '08:30 – 22:00' in text
        assert 'next day' not in text and '+1 day' not in text

    def test_real_overnight_and_24h_shifts_are_labelled(self, laundry):
        OpeningHours.objects.filter(laundry=laundry, day=1).update(
            opening_time=time(20), closing_time=time(2))
        OpeningHours.objects.filter(laundry=laundry, day=2).update(
            opening_time=time(0), closing_time=time(0))
        text = str(self._admin().hours_summary(laundry))
        assert '20:00 – 02:00 (closes next day)' in text
        assert 'Open 24 hours' in text

    def test_change_page_renders(self, laundry, client):
        admin = User.objects.create_user(
            email='vac-admin@example.com', phone='233555960002', password='StrongPass123!',
            role=User.Role.ADMIN, is_staff=True, is_superuser=True)
        client.force_login(admin)
        response = client.get(reverse('admin:laundries_laundry_change', args=[laundry.pk]))
        assert response.status_code == 200
        assert 'What customers see now' in response.content.decode()
