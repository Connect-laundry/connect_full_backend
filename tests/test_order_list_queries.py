"""Query-count guard for the customer order list.

OrderDetailSerializer pulls laundry, payment and coupon off each order. Without
select_related that is three extra queries per row, so a 20-order history costs
60+ round trips. These tests fail if the joins are ever dropped.
"""
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from ordering.models import LaunderableItem, Order, OrderItem
from payments.models import Payment
from users.models import User


def _build_orders(count):
    owner = User.objects.create_user(
        email='qc-owner@example.com', phone='233555930001',
        password='StrongPass123!', role=User.Role.OWNER)
    customer = User.objects.create_user(
        email='qc-customer@example.com', phone='233555930002', password='StrongPass123!')
    laundry = Laundry.objects.create(
        name='Query Count Laundry', description='d', address='Accra',
        latitude=5.6, longitude=-0.1, phone_number='0240000030', owner=owner,
        status=Laundry.ApprovalStatus.APPROVED, is_active=True)
    service_type = Category.objects.create(
        name='QC Wash', type=Category.CategoryType.SERVICE_TYPE)
    item = LaunderableItem.objects.create(name='QC Shirt')

    for index in range(count):
        order = Order.objects.create(
            user=customer, laundry=laundry, total_amount=Decimal('25.00'),
            pickup_date=timezone.now(), address='Accra',
            pickup_address='Accra', delivery_address='Accra')
        OrderItem.objects.create(
            order=order, item=item, service_type=service_type,
            name='QC Shirt', quantity=2, price=Decimal('10.00'))
        Payment.objects.create(
            user=customer, order=order, amount=Decimal('25.00'), currency='GHS',
            transaction_reference=f'ORD-QC-{index}',
            payment_method=Payment.Method.CARD, status=Payment.Status.PENDING)

    return customer


@pytest.mark.django_db
class TestOrderListQueryCount:
    def _list(self, customer):
        client = APIClient()
        client.force_authenticate(user=customer)
        return client.get(reverse('order-list'))

    def test_query_count_does_not_grow_with_order_count(self):
        """The real regression guard: adding orders must not add joins."""
        customer = _build_orders(3)

        with CaptureQueriesContext(connection) as ctx:
            response = self._list(customer)
        assert response.status_code == status.HTTP_200_OK
        baseline = len(ctx.captured_queries)

        # Add six more orders for the same customer.
        laundry = Laundry.objects.get(name='Query Count Laundry')
        item = LaunderableItem.objects.get(name='QC Shirt')
        service_type = Category.objects.get(name='QC Wash')
        for index in range(6):
            order = Order.objects.create(
                user=customer, laundry=laundry, total_amount=Decimal('25.00'),
                pickup_date=timezone.now(), address='Accra',
                pickup_address='Accra', delivery_address='Accra')
            OrderItem.objects.create(
                order=order, item=item, service_type=service_type,
                name='QC Shirt', quantity=1, price=Decimal('10.00'))
            Payment.objects.create(
                user=customer, order=order, amount=Decimal('25.00'), currency='GHS',
                transaction_reference=f'ORD-QC-EXTRA-{index}',
                payment_method=Payment.Method.CARD, status=Payment.Status.PENDING)

        with CaptureQueriesContext(connection) as ctx:
            response = self._list(customer)
        assert response.status_code == status.HTTP_200_OK
        grown = len(ctx.captured_queries)

        # price_breakdown still aggregates per order, so allow a small constant
        # per row — but the laundry/payment/coupon joins must not scale.
        per_order = (grown - baseline) / 6
        assert per_order <= 2, (
            f"{per_order:.1f} queries per extra order — the select_related joins "
            "on laundry/payment/coupon have probably been dropped."
        )

    def test_list_returns_joined_fields_without_error(self):
        customer = _build_orders(2)

        response = self._list(customer)

        assert response.status_code == status.HTTP_200_OK
        rows = response.data
        if isinstance(rows, dict):
            rows = rows.get('data', rows).get('results', rows)
        assert len(rows) == 2
        # These are exactly the fields that caused the extra queries.
        assert rows[0]['laundryName'] == 'Query Count Laundry'
        assert rows[0]['payment_reference'].startswith('ORD-QC')
        assert rows[0]['price_breakdown']['currency'] == 'GHS'
