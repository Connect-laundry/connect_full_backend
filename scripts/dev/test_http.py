from rest_framework_simplejwt.tokens import RefreshToken
from laundries.models.service import LaundryService
from users.models import User
import requests
import json
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()


def http_test():
    user = User.objects.filter(role='CUSTOMER').first()
    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)

    laundry_service = LaundryService.objects.filter(
        is_available=True,
        laundry__is_active=True,
        laundry__status='APPROVED').first()

    payload = {
        "laundry": str(laundry_service.laundry.id),
        "pickup_date": "2026-03-12T08:00:00Z",
        "pickup_address": "Test Address",
        "pickup_lat": 6.6726826,
        "pickup_lng": -1.5674371,
        "delivery_address": "Test Address",
        "delivery_lat": 6.6726826,
        "delivery_lng": -1.5674371,
        "items": [
            {
                "item": str(laundry_service.item.id),
                "service_type": str(laundry_service.service_type.id),
                "quantity": 3
            }
        ],
        "special_instructions": "Test via HTTP",
        "payment_method": "paystack"
    }

    print("Hitting http://localhost:8000/api/v1/booking/create/ ...")
    try:
        response = requests.post(
            'http://localhost:8000/api/v1/booking/create/',
            json=payload,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        print(f"Status Code: {response.status_code}")
        print("Response:", response.text)
    except Exception as e:
        print("Request failed:", e)


if __name__ == "__main__":
    http_test()
