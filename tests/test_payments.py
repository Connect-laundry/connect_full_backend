# pyre-ignore[missing-module]
import pytest

# pyre-ignore[missing-module]
from unittest.mock import patch

# pyre-ignore[missing-module]
from django.urls import reverse

# pyre-ignore[missing-module]
from rest_framework import status

# pyre-ignore[missing-module]
from payments.models import Payment

# pyre-ignore[missing-module]
from ordering.models import Order


@pytest.mark.django_db
class TestPaymentFlow:
    @patch("payments.services.paystack.PaystackService.initialize_transaction")
    def test_payment_initialization(
        self, mock_init, auth_client, sample_order
    ):
        mock_init.return_value = {
            "status": True,
            "data": {
                "authorization_url": "http://paystack.com/auth",
                "access_code": "ACCESS_123",
            },
        }

        url = reverse("payment_initialize")
        response = auth_client.post(
            url, {"order_id": sample_order.id, "payment_method": "CARD"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["data"]["authorization_url"] == "http://paystack.com/auth"
        created_reference = response.data["data"]["reference"]
        assert created_reference
        assert Payment.objects.filter(
            order=sample_order, user=sample_order.user, transaction_reference=created_reference
        ).exists()

    @patch("payments.services.paystack.PaystackService.verify_transaction")
    def test_payment_verification(self, mock_verify, auth_client, sample_payment):
        mock_verify.return_value = {
            "status": True,
            "data": {"status": "success", "amount": 10000},
        }

        url = reverse(
            "payment_verify", kwargs={"reference": sample_payment.transaction_reference}
        )
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        sample_payment.refresh_from_db()
        assert sample_payment.status == "SUCCESS"
        assert sample_payment.order.status == "CONFIRMED"
