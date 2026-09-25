"""
The laundry owner must never receive `order.handover_code` (or the
customer's OTP) from any API surface. A laundry that could read the
customer's own code back could submit it and falsely prove a delivery it
never made, forcing an immediate escrow release and payout to itself.

These are serializer-contract tests: they fail loudly the moment anyone adds
the field to an owner-facing serializer, rather than relying on someone
remembering to grep for it during review.
"""
import pytest
from django.urls import reverse
from rest_framework import status

from laundries.serializers.dashboard import DashboardOrderSerializer
from ordering.serializers.order import OrderDetailSerializer
from ordering.services.handover import ensure_handover_code


LEAK_FIELD_NAMES = {'handover_code', 'otp', 'delivery_code'}


@pytest.mark.django_db
class TestHandoverCodeSerializerContract:
    def test_order_detail_serializer_has_no_code_field(self, sample_order):
        ensure_handover_code(sample_order)

        assert LEAK_FIELD_NAMES.isdisjoint(OrderDetailSerializer.Meta.fields)
        data = OrderDetailSerializer(sample_order).data
        assert LEAK_FIELD_NAMES.isdisjoint(data.keys())

    def test_owner_dashboard_order_serializer_has_no_code_field(self, sample_order):
        ensure_handover_code(sample_order)

        assert LEAK_FIELD_NAMES.isdisjoint(DashboardOrderSerializer.Meta.fields)
        data = DashboardOrderSerializer(sample_order).data
        assert LEAK_FIELD_NAMES.isdisjoint(data.keys())

    def test_owner_order_detail_api_response_has_no_code_field(self, api_client, sample_order):
        # Belt-and-braces: even if a future refactor swaps the serializer,
        # the actual HTTP response the owner receives must stay clean.
        ensure_handover_code(sample_order)
        owner = sample_order.laundry.owner
        api_client.force_authenticate(user=owner)

        response = api_client.get(
            reverse('dashboard-orders-detail', kwargs={'pk': sample_order.id})
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        payload = body.get('data', body)
        assert sample_order.handover_code not in _flatten_values(payload)
        assert LEAK_FIELD_NAMES.isdisjoint(_flatten_keys(payload))


def _flatten_keys(obj, keys=None):
    if keys is None:
        keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            _flatten_keys(v, keys)
    elif isinstance(obj, list):
        for item in obj:
            _flatten_keys(item, keys)
    return keys


def _flatten_values(obj, values=None):
    if values is None:
        values = []
    if isinstance(obj, dict):
        for v in obj.values():
            _flatten_values(v, values)
    elif isinstance(obj, list):
        for item in obj:
            _flatten_values(item, values)
    else:
        values.append(obj)
    return values
