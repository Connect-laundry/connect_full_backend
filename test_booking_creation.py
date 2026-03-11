import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test.client import Client
from users.models import User
from laundries.models import Laundry, Category
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem
from rest_framework_simplejwt.tokens import RefreshToken

def run_test():
    print("--- Starting End-to-End Booking Creation Test ---")
    
    # 1. Get a test user
    user = User.objects.filter(role='CUSTOMER').first()
    if not user:
        user = User.objects.create_user(email='test_customer@example.com', password='password123', role='CUSTOMER', first_name='Test', last_name='Customer', phone='+233201234567')
        print(f"Created temporary user: {user.email}")
    else:
        print(f"Using existing user: {user.email}")

    # Generate JWT token for auth
    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)

    # 2. Get valid IDs for the payload
    # Find a laundry that has active services
    laundry_service = LaundryService.objects.filter(is_available=True, laundry__is_active=True, laundry__status='APPROVED').first()
    
    if not laundry_service:
        print("ERROR: No active laundry services found in the database. Cannot execute test.")
        return

    laundry = laundry_service.laundry
    item = laundry_service.item
    service_type = laundry_service.service_type

    print(f"Using Laundry ID: {laundry.id}")
    print(f"Using Item ID: {item.id}")
    print(f"Using Service Type ID: {service_type.id}")

    # 3. Construct the exact frontend payload
    payload = {
        "laundry": str(laundry.id),
        "pickup_date": "2026-03-12T08:00:00Z",
        "pickup_address": "P.V. Obeng Avenue, Oforikrom, Ghana",
        "pickup_lat": 6.6726826,
        "pickup_lng": -1.5674371,
        "delivery_address": "P.V. Obeng Avenue, Oforikrom, Ghana",
        "delivery_lat": 6.6726826,
        "delivery_lng": -1.5674371,
        "items": [
            {
                "item": str(item.id),
                "service_type": str(service_type.id),
                "quantity": 3
            }
        ],
        "special_instructions": "Please handle with care.",
        "payment_method": "paystack"
    }

    print("\nSimulating Frontend Payload:")
    print(json.dumps(payload, indent=2))

    # 4. Make the HTTP Request
    client = Client()
    print("\nSending POST request to /api/v1/booking/create/...")
    
    response = client.post(
        '/api/v1/booking/create/',
        data=json.dumps(payload),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {access_token}'
    )

    print(f"\nResponse Status Code: {response.status_code}")
    
    # Try to parse response content
    try:
        response_data = response.json()
        print("\nResponse Body:")
        print(json.dumps(response_data, indent=2))
        
        if response.status_code == 201:
            print("\nTEST PASSED: Booking created successfully without 500 errors!")
            # Verify the order actually exists and has GPS coords
            order_id = response_data.get('data', {}).get('id') or response_data.get('id')  # Depending on whether there's an envelope
            if order_id:
                from ordering.models import Order
                order = Order.objects.get(id=order_id)
                print(f"Verified in DB -> Order Total: {order.total_amount}, Pickup Lat: {order.pickup_lat}, Payment Method in Paystack object.")
        else:
            print("\nTEST FAILED: Endpoint did not return 201 Created.")
            
    except Exception as e:
        print("\nFailed to parse response JSON or verify DB.")
        print("Raw Response Content:")
        print(response.content.decode('utf-8', errors='replace'))
        print(e)

if __name__ == '__main__':
    run_test()
