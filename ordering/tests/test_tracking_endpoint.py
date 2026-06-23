"""Tests for the customer-facing GET /orders/{id}/tracking/ aggregator."""
import pytest
from django.urls import reverse
from rest_framework import status

from ordering.models import Order, OrderItem
from ordering.services.order_state_machine import OrderStateMachine
from ordering.views.tracking_view import derive_otp
from users.models import User


@pytest.fixture
def order_with_item(sample_order):
    OrderItem.objects.create(
        order=sample_order,
        name="Shirt",
        quantity=3,
        price="10.00",
    )
    return sample_order


@pytest.mark.django_db
class TestOrderTrackingEndpoint:
    def test_owner_can_fetch_tracking_snapshot(self, api_client, order_with_item, authenticated_user):
        api_client.force_authenticate(user=authenticated_user)

        url = reverse('order-tracking', kwargs={'pk': order_with_item.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body['status'] == 'success'

        data = body['data']
        # Every top-level card must be present, even with no activity.
        assert set(data.keys()) >= {
            'order', 'timeline', 'items', 'charges', 'payment', 'laundry', 'activity', 'can_cancel',
        }

        # Order header
        assert data['order']['order_no'] == order_with_item.order_no
        assert data['order']['status'] == 'PENDING'
        assert data['order']['otp'] == derive_otp(order_with_item)
        assert data['order']['is_terminal'] is False
        assert data['order']['estimated_completion'] is not None

        # Timeline contains the canonical 6 milestones with PENDING current.
        labels = [m['label'] for m in data['timeline']]
        assert labels[0] == 'Order placed'
        assert labels[-1] == 'Delivered'
        assert data['timeline'][0]['is_current'] is True
        assert data['timeline'][0]['is_completed'] is False

        # Items round-trip with computed subtotal.
        assert len(data['items']) == 1
        assert data['items'][0]['quantity'] == 3
        assert data['items'][0]['unit_price'] == '10.00'
        assert data['items'][0]['subtotal'] == '30.00'

        # Charges + payment skeleton always present.
        assert data['charges']['currency'] == 'GHS'
        assert data['payment']['status'] == 'UNPAID'
        assert data['payment']['paid_amount'] == '0.00'

        # Customer can cancel a PENDING order.
        assert data['can_cancel'] is True

    def test_non_owner_customer_gets_404(self, api_client, order_with_item, db):
        intruder = User.objects.create_user(
            email='intruder@example.com',
            phone='233555990099',
            password='pass',
        )
        api_client.force_authenticate(user=intruder)

        url = reverse('order-tracking', kwargs={'pk': order_with_item.id})
        response = api_client.get(url)

        # OrderViewSet.get_queryset filters by request.user, so foreign orders 404.
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_gets_401(self, api_client, order_with_item):
        url = reverse('order-tracking', kwargs={'pk': order_with_item.id})
        response = api_client.get(url)

        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_progressed_order_marks_milestones_completed(self, api_client, order_with_item, authenticated_user):
        owner = order_with_item.laundry.owner
        OrderStateMachine.transition(order_with_item.id, Order.Status.CONFIRMED, user=owner)
        OrderStateMachine.transition(order_with_item.id, Order.Status.PICKED_UP, user=owner)

        api_client.force_authenticate(user=authenticated_user)
        url = reverse('order-tracking', kwargs={'pk': order_with_item.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        timeline = response.json()['data']['timeline']

        completed_labels = [m['label'] for m in timeline if m['is_completed']]
        current_labels = [m['label'] for m in timeline if m['is_current']]
        # Stages prior to current are completed; current stage is "in progress"
        # (not yet completed); later stages are future. Mirrors Uber-Eats/Domino's.
        assert 'Order placed' in completed_labels
        assert 'Confirmed by laundry' in completed_labels
        assert 'Picked up' not in completed_labels
        assert current_labels == ['Picked up']

        # Activity log captures the two transitions plus PENDING isn't recorded
        # by the state machine (it's the initial state) — so two entries here.
        activity = response.json()['data']['activity']
        assert len(activity) >= 2
        assert activity[-1]['new_status'] == Order.Status.PICKED_UP

    def test_terminal_cancelled_order_marks_terminal(self, api_client, order_with_item, authenticated_user):
        owner = order_with_item.laundry.owner
        OrderStateMachine.transition(order_with_item.id, Order.Status.CANCELLED, user=owner)

        api_client.force_authenticate(user=authenticated_user)
        url = reverse('order-tracking', kwargs={'pk': order_with_item.id})
        response = api_client.get(url)

        data = response.json()['data']
        assert data['order']['status'] == 'CANCELLED'
        assert data['order']['is_terminal'] is True
        assert data['order']['estimated_completion'] is None
        # No happy-path milestone is current for a branch-terminal order.
        assert not any(m['is_current'] for m in data['timeline'])
        assert data['can_cancel'] is False
