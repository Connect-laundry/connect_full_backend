
import os
import django
from django.conf import settings

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from rest_framework.test import APIRequestFactory
from laundries.views.laundry import LaundryViewSet

def reproduce():
    factory = APIRequestFactory()
    view = LaundryViewSet.as_view({'get': 'featured'})
    
    # Simulate a request to /api/v1/laundries/featured/ with location
    request = factory.get('/api/v1/laundries/featured/', {'lat': '5.6037', 'lng': '-0.1870'})
    
    try:
        response = view(request)
        print(f"Status Code: {response.status_code}")
        print(f"Response Data: {response.data}")
    except Exception as e:
        import traceback
        print("Caught exception:")
        traceback.print_exc()

if __name__ == "__main__":
    reproduce()
