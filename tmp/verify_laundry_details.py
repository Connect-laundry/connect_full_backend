import sys
import os
sys.path.append(os.getcwd())
import django
from django.conf import settings

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from laundries.models.laundry import Laundry
from laundries.serializers.laundry_detail import LaundryDetailSerializer
from django.test import RequestFactory

def verify_laundry_details():
    # 1. Get a laundry object
    laundry = Laundry.objects.first()
    if not laundry:
        print("No laundry found in database. Seed data first.")
        return

    # 2. Mock a request for the serializer context
    factory = RequestFactory()
    request = factory.get('/')
    from users.models import User
    user = User.objects.filter(is_superuser=True).first()
    if not user:
        user = User.objects.first()
    request.user = user

    # 3. Serialize
    serializer = LaundryDetailSerializer(laundry, context={'request': request})
    data = serializer.data

    # 4. Check for new fields
    new_fields = ['minOrder', 'deliveryFee']
    passed = True
    print("\n--- Verification Results ---")
    for field in new_fields:
        if field in data:
            print(f"[PASS] {field}: {data[field]}")
        else:
            print(f"[FAIL] {field} is missing from response")
            passed = False
    
    if passed:
        print("\nSUCCESS: All new fields are correctly present in LaundryDetailSerializer.")
    else:
        print("\nFAILURE: Some fields are missing.")

if __name__ == "__main__":
    verify_laundry_details()
