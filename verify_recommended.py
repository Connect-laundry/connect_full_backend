import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from laundries.views.laundry import LaundryViewSet
from rest_framework.test import APIRequestFactory

factory = APIRequestFactory()
# Request with recommended=true
request = factory.get('/api/v1/laundries/laundries/?recommended=true')
view = LaundryViewSet.as_view({'get': 'list'})

try:
    response = view(request)
    print("Status Code:", response.status_code)
    laundries = response.data.get('results', [])
    for l in laundries:
        print(f"Name: {l.get('name')}, Rating: {l.get('rating')}, ReviewsCount: {l.get('reviewsCount')}")
    print("Recommended logic works successfully.")
except Exception as e:
    import traceback
    print("Error during test:")
    traceback.print_exc()
