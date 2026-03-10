import os
import django
import sys
import json
from decimal import Decimal

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from ordering.views.order_views import CatalogViewSet
from users.models import User

def verify_booking_endpoints():
    print("\n--- Final Verification of Booking Endpoints ---")
    factory = APIRequestFactory()
    viewset = CatalogViewSet.as_view({'get': 'services', 'items': 'items'})
    
    # Create a mock user
    user = User.objects.first()
    if not user:
        print("[FAIL] No user found for authentication.")
        return

    # 1. Test /booking/services/
    request = factory.get('/api/v1/booking/services/')
    force_authenticate(request, user=user)
    response = viewset(request, action='services')
    
    print("\nEndpoint: /api/v1/booking/services/")
    if response.status_code == 200:
        results = response.data.get('results', [])
        print(f"[SUCCESS] Returned {len(results)} services.")
        for s in results:
            print(f" - {s['name']}")
    else:
        print(f"[FAIL] Status Code: {response.status_code}")

    # 2. Test /booking/items/
    request = factory.get('/api/v1/booking/items/')
    force_authenticate(request, user=user)
    response = viewset(request, action='items')
    
    print("\nEndpoint: /api/v1/booking/items/")
    if response.status_code == 200:
        results = response.data.get('results', [])
        print(f"[SUCCESS] Returned {len(results)} items.")
        for item in results:
            supported = [s['name'] for s in item.get('supported_services', [])]
            print(f" - {item['name']} ({item['item_category_name']}): {', '.join(supported)}")
    else:
        print(f"[FAIL] Status Code: {response.status_code}")

if __name__ == "__main__":
    verify_booking_endpoints()
