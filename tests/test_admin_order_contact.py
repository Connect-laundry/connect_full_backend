"""Owner/ops order dashboard (Django admin) surfaces customer phone + pickup.

Guards the OrderAdmin display methods, including the null-user / missing-phone
edge cases, so the order changelist and change form never 500.
"""
import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from laundries.models.laundry import Laundry
from ordering.models import Order
from users.models import User


def _make_order(customer_phone):
    admin = User.objects.create_superuser(
        email='ops-admin@example.com', phone='+233555990001', password='StrongPass123!',
    )
    owner = User.objects.create_user(
        email='ops-owner@example.com', phone='+233555990002', password='StrongPass123!',
        role=User.Role.OWNER,
    )
    # create() (not create_user) so we can exercise the no-phone edge case,
    # which the manager would otherwise reject.
    customer = User.objects.create(email='ops-customer@example.com', phone=customer_phone)
    laundry = Laundry.objects.create(
        name='Ops Laundry', description='x', address='Accra', city='Accra',
        latitude='5.6037', longitude='-0.1870', phone_number='0240001111',
        owner=owner, status=Laundry.ApprovalStatus.APPROVED, is_active=True,
    )
    order = Order.objects.create(
        user=customer, laundry=laundry, status='PENDING', total_amount='25.00',
        pickup_date=timezone.now(), pickup_address='12 Test Street, Accra',
    )
    return admin, order


@pytest.mark.django_db
@override_settings(ROOT_URLCONF='config.urls')
def test_order_changelist_shows_customer_phone(client):
    admin, order = _make_order('+233241234567')
    client.force_login(admin)

    response = client.get(reverse('admin:ordering_order_changelist'))
    assert response.status_code == 200
    assert b'+233241234567' in response.content


@pytest.mark.django_db
@override_settings(ROOT_URLCONF='config.urls')
def test_order_change_form_shows_phone_and_pickup(client):
    admin, order = _make_order('+233241234567')
    client.force_login(admin)

    response = client.get(reverse('admin:ordering_order_change', args=[order.id]))
    assert response.status_code == 200
    content = response.content.decode()
    assert '+233241234567' in content
    assert '12 Test Street, Accra' in content


@pytest.mark.django_db
@override_settings(ROOT_URLCONF='config.urls')
def test_order_admin_handles_customer_without_phone(client):
    # Defensive: an order whose customer somehow has no phone must not 500.
    admin, order = _make_order(None)
    client.force_login(admin)

    changelist = client.get(reverse('admin:ordering_order_changelist'))
    change = client.get(reverse('admin:ordering_order_change', args=[order.id]))
    assert changelist.status_code == 200
    assert change.status_code == 200
    assert 'Not provided' in change.content.decode()
