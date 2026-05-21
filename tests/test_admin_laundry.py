import pytest
from django.test import override_settings
from django.urls import reverse

from laundries.models.laundry import Laundry
from users.models import User


@pytest.mark.django_db
@override_settings(ROOT_URLCONF='config.urls')
def test_laundry_admin_change_view_loads(client):
    admin = User.objects.create_superuser(
        email='admin-change@example.com',
        phone='233555990001',
        password='StrongPass123!',
    )
    owner = User.objects.create_user(
        email='owner-change@example.com',
        phone='233555990002',
        password='StrongPass123!',
        role=User.Role.OWNER,
    )
    laundry = Laundry.objects.create(
        name='Admin Change Laundry',
        description='Admin change test',
        address='Accra',
        city='Accra',
        latitude='5.603700',
        longitude='-0.187000',
        phone_number='0240009999',
        owner=owner,
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
    )

    client.force_login(admin)

    response = client.get(reverse('admin:laundries_laundry_change', args=[laundry.id]))

    assert response.status_code == 200
