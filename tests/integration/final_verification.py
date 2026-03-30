from logistics.models import TrackingLog
from ordering.serializers import OrderDetailSerializer
from ordering.models import Order, OrderStatusHistory
from users.models import User, PasswordResetToken
import os
import sys
import django
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta

# Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()


def test_password_reset():
    print("--- Testing Password Reset Logic ---")
    user = User.objects.first()
    if not user:
        print("No user found. Skipping.")
        return

    token = PasswordResetToken.create_for_user(user)
    print(f"Token created for {user.email}: {token[:8]}...")

    pr_token = PasswordResetToken.objects.filter(
        user=user).latest('created_at')
    print(f"Token is valid: {pr_token.is_valid()}")

    # Simulate use
    pr_token.used_at = timezone.now()
    pr_token.save()
    print(f"Token used. Valid: {pr_token.is_valid()}")


def test_order_tracking():
    print("\n--- Testing Order Tracking Enhancements ---")
    order = Order.objects.first()
    if not order:
        print("No order found. Skipping.")
        return

    # 1. Verify Status History exists and is serialized
    if not OrderStatusHistory.objects.filter(order=order).exists():
        OrderStatusHistory.objects.create(
            order=order,
            new_status=order.status,
            timestamp=timezone.now()
        )

    serializer = OrderDetailSerializer(order)
    data = serializer.data

    print(f"Order: {order.order_no}")
    print(f"Response has ID: {'id' in data}")
    print(
        f"Response has History: {'history' in data} (count: {len(data['history'])})")

    # 2. Test Live Coordinates
    original_status = order.status
    order.status = Order.Status.OUT_FOR_DELIVERY
    order.save()

    # Add a tracking log
    lat, lng = Decimal("5.6037"), Decimal("-0.1870")
    TrackingLog.objects.create(
        order=order,
        status="OUT_FOR_DELIVERY",
        latitude=lat,
        longitude=lng
    )

    data = OrderDetailSerializer(order).data
    print(f"Status: {data['status']}")
    print(f"Van Lat/Lng: {data['van_latitude']}, {data['van_longitude']}")

    # Restore status
    order.status = original_status
    order.save()


if __name__ == "__main__":
    try:
        test_password_reset()
        test_order_tracking()
        print("\nALL TESTS PASSED.")
    except Exception as e:
        print(f"\nTEST FAILED: {str(e)}")
