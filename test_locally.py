import json
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from laundries.models.laundry import Laundry

def test_discovery():
    client = APIClient()
    User = get_user_model()
    user, _ = User.objects.get_or_create(email="test_tester@test.com", defaults={"phone": "9999999999"})
    client.force_authenticate(user=user)

    def print_res(name, res):
        print(f"\n--- {name} ---")
        print(f"Status: {res.status_code}")
        try:
            data = json.loads(res.content)
            print(f"JSON Output: {json.dumps(data, indent=2)}")
        except:
            print(f"Non-JSON Output: {res.content[:200]}")

    # 1. Test is_featured
    res = client.get('/api/v1/laundries/laundries/?is_featured=true')
    print_res("Featured Laundries", res)

    # 2. Test nearby
    res = client.get('/api/v1/laundries/laundries/?nearby=true&lat=5.6&lng=-0.1')
    print_res("Nearby Search", res)

    # 3. Test Diagnosis
    res = client.get('/api/v1/laundries/diagnosis/')
    print_res("Diagnosis", res)

if __name__ == "__main__":
    test_discovery()
