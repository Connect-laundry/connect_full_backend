import os
import django
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from laundries.models.laundry import Laundry
from users.models import User

def test_create_laundry():
    try:
        u = User.objects.filter(role='OWNER').first()
        if not u:
            print("No owner found")
            return
        
        print(f"Using user: {u.email}")
        
        laundry = Laundry.objects.create(
            owner=u,
            name='Test Laundry',
            address='Test Addr',
            city='Accra',
            latitude=5.6037,
            longitude=-0.1870,
            phone_number='0240000000',
            price_range='$$',
            estimated_delivery_hours=24,
            delivery_fee=0,
            pickup_fee=0,
            min_order=0,
            price_per_kg=10,
            pricing_methods=['PER_KG']
        )
        print(f"Success: {laundry.id}")
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    test_create_laundry()
