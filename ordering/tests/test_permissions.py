import pytest
from rest_framework import status
from django.urls import reverse
from users.models import User

@pytest.mark.django_db
class TestOrderPermissions:
    def test_owner_can_accept_order(self, api_client, sample_order):
        owner = sample_order.laundry.owner
        sample_order.payment_method = sample_order.PaymentMethod.CASH
        sample_order.save(update_fields=['payment_method', 'updated_at'])
        api_client.force_authenticate(user=owner)
        
        url = reverse('order-lifecycle-accept', kwargs={'pk': sample_order.id})
        response = api_client.patch(url)
        
        assert response.status_code == status.HTTP_200_OK

    def test_other_laundry_owner_cannot_accept_order(self, api_client, sample_order, other_owner):
        api_client.force_authenticate(user=other_owner)
        
        url = reverse('order-lifecycle-accept', kwargs={'pk': sample_order.id})
        response = api_client.patch(url)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_customer_cannot_accept_order(self, api_client, sample_order, authenticated_user):
        api_client.force_authenticate(user=authenticated_user)
        
        url = reverse('order-lifecycle-accept', kwargs={'pk': sample_order.id})
        response = api_client.patch(url)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_can_accept_order(self, api_client, sample_order, admin_user):
        sample_order.payment_method = sample_order.PaymentMethod.CASH
        sample_order.save(update_fields=['payment_method', 'updated_at'])
        api_client.force_authenticate(user=admin_user)
        
        url = reverse('order-lifecycle-accept', kwargs={'pk': sample_order.id})
        response = api_client.patch(url)
        
        assert response.status_code == status.HTTP_200_OK
