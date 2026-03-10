import os
import sys

# Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.test import Client
from users.models import User
from laundries.models import Laundry

c = Client()
# Authenticate
user = User.objects.filter(is_active=True).first()
if getattr(user, 'is_staff', False) == False:
    # Just grab any owner/staff or regular user for auth
    pass
c.force_login(user)

print("--- Testing Catalog Items Endpoint ---")
response = c.get('/api/v1/booking/catalog/items/')
print(f"Status Code: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    items = data.get('results', [])
    print(f"Total Items: {len(items)}")
    if items:
        print(f"Sample Item: {items[0]}")

print("\n--- Testing Laundry Services Endpoint ---")
laundry = Laundry.objects.first()
if laundry:
    response = c.get(f'/api/v1/laundries/laundries/{laundry.id}/services/')
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        services = data.get('results', [])
        print(f"Total Laundry Services for '{laundry.name}': {len(services)}")
        if services:
            print(f"Sample Service: {services[0]}")
    else:
        print(f"Error Content: {response.content}")
else:
    print("No laundry records found to test.")
