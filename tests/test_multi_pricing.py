import pytest
from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from laundries.models import Laundry, Category, LaundryService
from ordering.models import Order, LaunderableItem


@pytest.fixture
def api_client(authenticated_user):
    client = APIClient()
    client.force_authenticate(user=authenticated_user)
    return client


@pytest.fixture
def pricing_setup(db):
    """Sets up basic categories and items for pricing tests."""
    service_type = Category.objects.create(name="Wash & Iron", type="SERVICE_TYPE")
    item_cat = Category.objects.create(name="Clothing", type="ITEM_CATEGORY")
    item = LaunderableItem.objects.create(name="Shirt", item_category=item_cat)
    return {"service_type": service_type, "item": item}


@pytest.mark.django_db
class TestMultiPricingWorkflow:

    def test_per_item_order_creation(self, api_client, sample_laundry, pricing_setup):
        # 1. Setup Laundry for Per Item
        sample_laundry.pricing_methods = ["PER_ITEM"]

        # Add a service price (required for validation now)
        LaundryService.objects.create(
            laundry=sample_laundry,
            item=pricing_setup["item"],
            service_type=pricing_setup["service_type"],
            price=Decimal("5.00"),
        )
        sample_laundry.save()

        # 2. Create Order
        url = reverse("booking-create")
        if not url.endswith("/"):
            url += "/"

        data = {
            "laundry": sample_laundry.id,
            "pickup_date": "2026-04-01T10:00:00Z",
            "pricing_method": "PER_ITEM",
            "items": [
                {
                    "item": pricing_setup["item"].id,
                    "service_type": pricing_setup["service_type"].id,
                    "quantity": 2,
                }
            ],
            "pickup_address": "Test Address",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        order = Order.objects.get(id=response.data["id"])
        assert order.pricing_method == "PER_ITEM"
        assert order.estimated_price >= Decimal("10.00")
        assert order.final_price == order.estimated_price

    def test_per_kg_order_and_weight_update(self, api_client, sample_laundry):
        # 1. Setup Laundry for Per Kg
        sample_laundry.pricing_methods = ["PER_KG"]
        sample_laundry.price_per_kg = Decimal("10.00")
        sample_laundry.min_weight = Decimal("2.00")
        sample_laundry.save()

        # 2. Create Order (Estimated)
        url = reverse("booking-create")
        if not url.endswith("/"):
            url += "/"
        data = {
            "laundry": sample_laundry.id,
            "pickup_date": "2026-04-01T10:00:00Z",
            "pricing_method": "PER_KG",
            "estimated_weight": 5.0,
            "pickup_address": "Test Address",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        order = Order.objects.get(id=response.data["id"])
        assert order.pricing_method == "PER_KG"
        assert order.estimated_price >= Decimal("50.00")
        assert order.final_price == Decimal("0.00")

        # 3. Staff Updates Weight
        weight_url = reverse("order-update-weight", kwargs={"pk": order.id})
        if not weight_url.endswith("/"):
            weight_url += "/"

        weight_data = {"actual_weight": 6.5}
        response = api_client.patch(weight_url, weight_data, format="json")
        assert response.status_code == status.HTTP_200_OK

        order.refresh_from_db()
        assert order.actual_weight == Decimal("6.50")
        assert order.final_price >= Decimal("65.00")
        assert order.status == "WEIGHED"

    def test_per_item_validation_requires_items(self, sample_laundry):
        """Ensures laundry cannot enable PER_ITEM without items in catalog."""
        from django.core.exceptions import ValidationError

        sample_laundry.pricing_methods = ["PER_ITEM"]
        # Laundry has no items (services) yet
        with pytest.raises(ValidationError) as excinfo:
            sample_laundry.full_clean()
        assert "At least one item/service must be added" in str(excinfo.value)

    def test_per_kg_validation_failure(self, api_client, sample_laundry):
        sample_laundry.pricing_methods = ["PER_KG"]
        sample_laundry.price_per_kg = Decimal("10.00")
        sample_laundry.save()

        url = reverse("booking-create")
        if not url.endswith("/"):
            url += "/"
        data = {
            "laundry": sample_laundry.id,
            "pickup_date": "2026-04-01T10:00:00Z",
            "pickup_address": "Test Address",
            "pricing_method": "PER_KG",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "estimated_weight" in response.data

    def test_per_kg_unsupported_failure(self, api_client, sample_laundry):
        # We need to add an item so PER_ITEM is valid during save
        pricing_setup = {
            "item": LaunderableItem.objects.create(name="Shirt"),
            "service_type": Category.objects.create(name="Wash", type="SERVICE_TYPE"),
        }
        LaundryService.objects.create(
            laundry=sample_laundry,
            item=pricing_setup["item"],
            service_type=pricing_setup["service_type"],
            price=Decimal("5.00"),
        )
        sample_laundry.pricing_methods = ["PER_ITEM"]
        sample_laundry.price_per_kg = Decimal("0.00")
        sample_laundry.save()

        url = reverse("booking-create")
        if not url.endswith("/"):
            url += "/"
        data = {
            "laundry": sample_laundry.id,
            "pickup_date": "2026-04-01T10:00:00Z",
            "pickup_address": "Test Address",
            "pricing_method": "PER_KG",
            "estimated_weight": 5.0,
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not support weight-based pricing" in str(response.data)
