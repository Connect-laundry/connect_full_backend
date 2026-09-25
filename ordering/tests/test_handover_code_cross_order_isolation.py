"""
A handover code proves one specific delivery, for one specific order, at one
specific laundry. These tests prove the code can never cross that boundary:
Laundry B's code must never confirm Laundry A's order, and Laundry B's owner
must never even reach Laundry A's order to try.
"""
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from ordering.models import Order
from ordering.services.finance_service import FinanceService
from ordering.services.handover import ensure_handover_code
from payments.services.settlement_service import SettlementService

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, OrderItem
from users.models import User


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _laundry_order_out_for_delivery(suffix):
    owner = User.objects.create_user(
        email=f'owner-{suffix}@example.com',
        phone=f'23355590{suffix}',
        password='StrongPass123!',
        role=User.Role.OWNER,
    )
    customer = User.objects.create_user(
        email=f'customer-{suffix}@example.com',
        phone=f'23355591{suffix}',
        password='StrongPass123!',
    )
    service_type = Category.objects.create(name=f'Wash {suffix}', type=Category.CategoryType.SERVICE_TYPE)
    item_category = Category.objects.create(name=f'Shirts {suffix}', type=Category.CategoryType.ITEM_CATEGORY)
    item = LaunderableItem.objects.create(name=f'Shirt {suffix}', item_category=item_category)
    laundry = Laundry.objects.create(
        name=f'Laundry {suffix}',
        description='Isolation test laundry',
        address='Accra',
        city='Accra',
        latitude='5.6037',
        longitude='-0.1870',
        phone_number=f'024000{suffix}',
        owner=owner,
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
    )
    LaundryService.objects.create(
        laundry=laundry, item=item, service_type=service_type, price='25.00', is_available=True,
    )
    from django.utils import timezone
    from datetime import timedelta
    order = Order.objects.create(
        user=customer, laundry=laundry, pickup_date=timezone.now() + timedelta(days=1),
        total_amount='25.00', pickup_address='Pickup', delivery_address='Delivery',
    )
    OrderItem.objects.create(order=order, item=item, service_type=service_type, name='Shirt', quantity=1, price='25.00')

    with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
        FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    order.payment_status = Order.PaymentStatus.PAID
    order.status = Order.Status.OUT_FOR_DELIVERY
    order.save()
    ensure_handover_code(order)
    SettlementService.record_for_order(order)
    return owner, customer, order


@pytest.mark.django_db
class TestHandoverCodeCrossOrderIsolation:
    @pytest.fixture(autouse=True)
    def _fresh_counters(self):
        cache.clear()
        yield
        cache.clear()

    def _deliver(self, owner, order, code):
        client = _auth_client(owner)
        url = reverse('order-lifecycle-mark-delivered', kwargs={'pk': order.id})
        return client.patch(url, {'handover_code': code}, format='json')

    def test_order_a_code_rejected_on_order_b(self):
        owner_a, _, order_a = _laundry_order_out_for_delivery('a1')
        owner_b, _, order_b = _laundry_order_out_for_delivery('b1')

        response = self._deliver(owner_b, order_b, order_a.handover_code)

        order_b.refresh_from_db()
        if order_a.handover_code == order_b.handover_code:
            # Astronomically unlikely collision: same code, but it is *also*
            # order_b's own real code, so this would legitimately succeed.
            # Re-roll isolation with a guaranteed-different wrong code instead.
            wrong = '9999' if order_a.handover_code != '9999' else '1234'
            response = self._deliver(owner_b, order_b, wrong)
            order_b.refresh_from_db()

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert order_b.status == Order.Status.OUT_FOR_DELIVERY
        assert order_b.delivery_confirmed_by_code is False
        assert SettlementService.outstanding_total(order_b.laundry) == Decimal('0.00')

    def test_order_b_code_rejected_on_order_a(self):
        owner_a, _, order_a = _laundry_order_out_for_delivery('a2')
        _, _, order_b = _laundry_order_out_for_delivery('b2')

        wrong = order_b.handover_code
        if wrong == order_a.handover_code:
            wrong = '9999' if order_a.handover_code != '9999' else '1234'

        response = self._deliver(owner_a, order_a, wrong)

        order_a.refresh_from_db()
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert order_a.status == Order.Status.OUT_FOR_DELIVERY
        assert order_a.delivery_confirmed_by_code is False

    def test_laundry_b_owner_cannot_even_reach_laundry_a_order(self):
        owner_a, _, order_a = _laundry_order_out_for_delivery('a3')
        owner_b, _, order_b = _laundry_order_out_for_delivery('b3')

        response = self._deliver(owner_b, order_a, order_a.handover_code)

        order_a.refresh_from_db()
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert order_a.status == Order.Status.OUT_FOR_DELIVERY
        assert order_a.delivery_confirmed_by_code is False
        assert SettlementService.outstanding_total(order_a.laundry) == Decimal('0.00')

    def test_each_owner_confirming_their_own_order_with_their_own_code_works(self):
        owner_a, _, order_a = _laundry_order_out_for_delivery('a4')
        owner_b, _, order_b = _laundry_order_out_for_delivery('b4')

        response_a = self._deliver(owner_a, order_a, order_a.handover_code)
        response_b = self._deliver(owner_b, order_b, order_b.handover_code)

        assert response_a.status_code == status.HTTP_200_OK
        assert response_b.status_code == status.HTTP_200_OK
        order_a.refresh_from_db()
        order_b.refresh_from_db()
        assert order_a.delivery_confirmed_by_code is True
        assert order_b.delivery_confirmed_by_code is True
        assert SettlementService.outstanding_total(order_a.laundry) == order_a.total_amount
        assert SettlementService.outstanding_total(order_b.laundry) == order_b.total_amount
        # No cross-contamination of amounts between the two laundries.
        assert order_a.laundry_id != order_b.laundry_id
