from django.contrib.auth import get_user_model
from logistics.models import TrackingLog
from ordering.serializers import OrderDetailSerializer
from ordering.models import Order, OrderStatusHistory
import os
import sys
import django
from decimal import Decimal

# Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


User = get_user_model()


def verify_tracking():
    print("Verifying Order Tracking Enhancements...")

    # Try to find an existing order or mock a check
    order = Order.objects.first()
    if not order:
        print("No orders found to verify. Skipping DB-based test.")
        return

    print(f"Testing with Order: {order.order_no}")

    # 1. Test Serializer
    serializer = OrderDetailSerializer(order)
    data = serializer.data

    print(f"ID in response: {'id' in data}")
    print(f"History in response: {'history' in data}")
    print(f"Van Latitude in response: {'van_latitude' in data}")

    # 2. Test Live Coordinates for OUT_FOR_DELIVERY
    original_status = order.status
    order.status = Order.Status.OUT_FOR_DELIVERY
    order.save()

    # Create a mock tracking log
    TrackingLog.objects.create(
        order=order,
        status="OUT_FOR_DELIVERY",
        latitude=Decimal("5.6037"),
        longitude=Decimal("-0.1870"),
    )

    serializer = OrderDetailSerializer(order)
    data = serializer.data

    print(f"Status: {data['status']}")
    print(f"Van Latitude (expect 5.6037): {data['van_latitude']}")
    print(f"Van Longitude (expect -0.1870): {data['van_longitude']}")

    # Cleanup/Restore
    order.status = original_status
    order.save()

    print("\nVERIFICATION COMPLETE.")


if __name__ == "__main__":
    verify_tracking()
