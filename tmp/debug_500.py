import os
import sys
import django

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import traceback
from rest_framework.test import APIRequestFactory, force_authenticate
from ordering.views.order_views import CatalogViewSet
from users.models import User

def debug_500():
    factory = APIRequestFactory()
    user = User.objects.first()
    if not user:
        print("No user found!")
        return

    # 1. Services endpoint
    print("\n--- Testing /services/ ---")
    viewset = CatalogViewSet.as_view({'get': 'services'})
    request = factory.get('/api/v1/booking/services/')
    force_authenticate(request, user=user)
    
    try:
        response = viewset(request)
        print(f"Status: {response.status_code}")
        print(f"Data: {response.data}")
    except Exception:
        traceback.print_exc()

    # 2. Items endpoint
    print("\n--- Testing /items/ ---")
    viewset = CatalogViewSet.as_view({'get': 'items'})
    request = factory.get('/api/v1/booking/items/')
    force_authenticate(request, user=user)
    
    try:
        response = viewset(request)
        print(f"Status: {response.status_code}")
        print(f"Data: {response.data}")
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    debug_500()
