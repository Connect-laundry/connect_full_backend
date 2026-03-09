import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DEBUG'] = 'False'

import django
django.setup()

from marketplace.models.special_offer import SpecialOffer
from marketplace.views.special_offer import SpecialOfferSerializer
from laundries.models.laundry import Laundry
from laundries.serializers.laundry_list import LaundryListSerializer
from users.models import User
import uuid

print("DEBUG is:", os.environ.get('DEBUG'))

try:
    u = User.objects.create(email="testowner@test.com", password="pwd", role="OWNER")
except Exception:
    u = User.objects.filter(role="OWNER").first()

try:
    so = SpecialOffer.objects.create(title="Test", image=f"fake_{uuid.uuid4()}.jpg")
    print("Created SpecialOffer")
except Exception as e:
    print("Failed to create special offer:", e)

try:
    laun = Laundry.objects.create(
        name=f"Test Laundry {uuid.uuid4()}",
        address="123", city="Accra",
        latitude=0, longitude=0, phone_number="123",
        owner=u,
        image=f"fake_{uuid.uuid4()}.jpg",
        is_active=True,
        is_featured=True,
        status="APPROVED"
    )
    print("Created Laundry")
except Exception as e:
    print("Failed to create laundry:", e)

# Now serialize SpecialOffer
so = SpecialOffer.objects.first()
try:
    sz = SpecialOfferSerializer(so)
    data = sz.data
    print("SpecialOffer Serialized Data:", data)
except Exception as e:
    print(f"SpecialOffer Serialization Error: {type(e).__name__}: {str(e)}")

# Now serialize Laundry
laun = Laundry.objects.first()
try:
    sz2 = LaundryListSerializer(laun)
    data2 = sz2.data
    print("Laundry Serialized Data:", data2)
except Exception as e:
    print(f"Laundry Serialization Error: {type(e).__name__}: {str(e)}")
