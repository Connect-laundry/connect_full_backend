import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, Order

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
    owner, _ = User.objects.get_or_create(
        email="testowner@example.com",
        defaults={"phone": "0240000001", "role": User.Role.OWNER},
    )
    owner.role = User.Role.OWNER
    owner.set_password("password123")
    owner.save()

    user, _ = User.objects.get_or_create(email="testuser@example.com", defaults={"phone": "0240000002"})
    user.set_password("password123")
    user.save()
    
    service_type, _ = Category.objects.get_or_create(
        name="Wash & Fold",
        defaults={"type": Category.CategoryType.SERVICE_TYPE},
    )
    item_category, _ = Category.objects.get_or_create(
        name="Everyday Wear",
        defaults={"type": Category.CategoryType.ITEM_CATEGORY},
    )
    item, _ = LaunderableItem.objects.get_or_create(
        name="Shirt",
        defaults={"item_category": item_category, "is_active": True},
    )
    laundry, _ = Laundry.objects.get_or_create(
        name="Test Laundry", 
        defaults={
            "address": "123 Street", 
            "city": "Accra", 
            "latitude": 5.6, 
            "longitude": -0.2,
            "owner": owner,
            "status": "APPROVED",
            "is_active": True
        }
    )
    LaundryService.objects.update_or_create(
        laundry=laundry,
        item=item,
        service_type=service_type,
        defaults={"price": "25.00", "is_available": True},
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
        defaults={
            "pickup_date": timezone.now() + timedelta(days=1),
            "pickup_address": "test pickup addr",
            "delivery_address": "test delivery addr",
        },
    )
    res = client.get('/api/v1/orders/active/')
    print(f"Active Orders API: {res.status_code}")
    # Fix for ReturnList/ReturnDict if necessary, usually json.dumps handles them if simple enough
    print(json.dumps(res.data, indent=2, cls=UUIDEncoder))

if __name__ == "__main__":
    test_apis()
