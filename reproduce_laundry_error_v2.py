
import os
import django
from django.conf import settings

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from rest_framework.test import APIRequestFactory
from laundries.views.laundry import LaundryViewSet
from laundries.models.laundry import Laundry

def reproduce():
    factory = APIRequestFactory()
    view = LaundryViewSet.as_view({'get': 'featured'})
    
    featured_laundries = Laundry.objects.filter(is_featured=True, status='APPROVED', is_active=True)
    print(f"Testing {featured_laundries.count()} featured laundries individually...")
    
    for laundry in featured_laundries:
        print(f"Testing laundry: {laundry.name} ({laundry.id})")
        # Try with and without location
        requests = [
            factory.get('/api/v1/laundries/featured/'),
            factory.get('/api/v1/laundries/featured/', {'lat': '5.6037', 'lng': '-0.1870'}),
            factory.get('/api/v1/laundries/featured/', {'lat': 'undefined', 'lng': 'undefined'})
        ]
        
        for req in requests:
            try:
                # We need to filter the queryset in the view to just THIS laundry
                # to isolate it. But get_queryset() is internal.
                # So we'll just call the view and let it process all of them,
                # but if it fails for any one during serialization, it will fail for the whole request.
                pass
            except Exception as e:
                import traceback
                print(f"Failed for request {req.GET}")
                traceback.print_exc()

    # Actually, the best way is to serialize them one by one
    from laundries.serializers.laundry_list import LaundryListSerializer
    
    for laundry in featured_laundries:
        print(f"Serializing laundry: {laundry.name}")
        contexts = [
            {'request': factory.get('/')},
            {'request': factory.get('/', {'lat': '5.6037', 'lng': '-0.1870'})},
            {'request': factory.get('/', {'lat': 'undefined', 'lng': 'undefined'})},
            {'request': None}
        ]
        for ctx in contexts:
            try:
                serializer = LaundryListSerializer(laundry, context=ctx)
                data = serializer.data
                # print(f"  Success {ctx.get('request').GET if ctx.get('request') else 'No Request'}")
            except Exception as e:
                print(f"  FAILED for {laundry.name} with context {ctx.get('request').GET if ctx.get('request') else 'No Request'}: {e}")
                import traceback
                traceback.print_exc()

if __name__ == "__main__":
    reproduce()
