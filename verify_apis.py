import json
from django.test import Client
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from laundries.models.category import Category
from laundries.models.laundry import Laundry
from ordering.models import Order

User = get_user_model()

class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, 'hex'):
            return obj.hex
        return str(obj)

def test_apis():
    client = APIClient()
    
    # 1. Setup Test Data
    User = get_user_model()
    user, _ = User.objects.get_or_create(email="testuser@example.com", defaults={"phone": "0240000002"})
    user.set_password("password123")
    user.save()
    
    category, _ = Category.objects.get_or_create(name="Wash & Fold")
    laundry, _ = Laundry.objects.get_or_create(
        name="Test Laundry", 
        defaults={
            "address": "123 Street", 
            "city": "Accra", 
            "latitude": 5.6, 
            "longitude": -0.2,
            "owner": user,
            "status": "APPROVED",
            "is_active": True
        }
    )
    
    # 2. Authenticate
    client.force_authenticate(user=user)
    
    print("\n--- Testing API Endpoints ---")
    
    # Test 1: Categories
    res = client.get('/api/v1/laundries/categories/')
    print(f"Categories API: {res.status_code}")
    print(json.dumps(res.data, indent=2, cls=UUIDEncoder))
    
    # Test 2: Unread Count
    res = client.get('/api/v1/support/notifications/unread-count/')
    print(f"Unread Count API: {res.status_code}")
    print(json.dumps(res.data, indent=2, cls=UUIDEncoder))
    
    # Test 3: Supported Cities
    res = client.get('/api/v1/addresses/supported-cities/')
    print(f"Supported Cities API: {res.status_code}")
    print(json.dumps(res.data, indent=2, cls=UUIDEncoder))
    
    # Test 4: Active Orders
    # Create a dummy order
    Order.objects.get_or_create(
        user=user, 
        laundry=laundry, 
        status=Order.Status.PENDING,
        defaults={"pickup_date": "2026-03-08T12:00:00Z", "address": "test addr"}
    )
    res = client.get('/api/v1/booking/orders/active/')
    print(f"Active Orders API: {res.status_code}")
    # Fix for ReturnList/ReturnDict if necessary, usually json.dumps handles them if simple enough
    print(json.dumps(res.data, indent=2, cls=UUIDEncoder))

if __name__ == "__main__":
    test_apis()
