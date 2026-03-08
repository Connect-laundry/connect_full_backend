import json
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from laundries.models.laundry import Laundry

User = get_user_model()

def reproduce():
    client = APIClient()
    user, _ = User.objects.get_or_create(email="test@test.com", defaults={"phone": "0000000001"})
    client.force_authenticate(user=user)
    
    print("\n--- Testing Discovery Endpoints ---")
    
    # 1. Test is_featured
    # The frontend guy says they use ?is_featured=true
    res = client.get('/api/v1/laundries/laundries/?is_featured=true')
    print(f"is_featured=true: {res.status_code}")
    if res.status_code == 500:
        print(res.content[:500])
        
    # 2. Test featured (the actual filter name)
    res = client.get('/api/v1/laundries/laundries/?featured=true')
    print(f"featured=true: {res.status_code}")
    
    # 3. Test nearby
    res = client.get('/api/v1/laundries/laundries/?nearby=true&lat=5.6&lng=-0.2&radius=10')
    print(f"nearby search: {res.status_code}")
    if res.status_code == 500:
        print(res.content[:500])

    # 4. Test nearby with invalid radius
    res = client.get('/api/v1/laundries/laundries/?nearby=true&lat=5.6&lng=-0.2&radius=invalid')
    print(f"nearby search invalid radius: {res.status_code}")

if __name__ == "__main__":
    reproduce()
