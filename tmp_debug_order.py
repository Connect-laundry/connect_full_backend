import os
import django
import traceback
from decimal import Decimal
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from laundries.models.laundry import Laundry
from laundries.models.category import Category
from ordering.models import LaunderableItem, Order, OrderItem
from users.models import User

def test_create_order():
    try:
        cust = User.objects.filter(role='CUSTOMER').first()
        owner = User.objects.filter(role='OWNER').first()
        laundry = Laundry.objects.filter(owner=owner).first()
        category = Category.objects.filter(type='SERVICE_TYPE').first()
        item = LaunderableItem.objects.first()
        
        if not all([cust, owner, laundry, category, item]):
            print(f"Missing data: cust={bool(cust)}, owner={bool(owner)}, laundry={bool(laundry)}, category={bool(category)}, item={bool(item)}")
            return
        
        print(f"Using Customer: {cust.email}, Laundry: {laundry.name}")
        
        from ordering.serializers.order import OrderCreateSerializer
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request
        
        factory = APIRequestFactory()
        request = factory.post('/api/v1/booking/create/')
        request.user = cust
        
        data = {
            "laundry": str(laundry.id),
            "pickup_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "pickup_address": "Test Location",
            "delivery_address": "Test Location",
            "items": [
                {
                    "item": str(item.id),
                    "service_type": str(category.id),
                    "quantity": 2
                }
            ]
        }
        
        serializer = OrderCreateSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            order = serializer.save()
            print(f"Success: Order {order.order_no}")
        else:
            print(f"Validation Failed: {serializer.errors}")
            
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    test_create_order()
