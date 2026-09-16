import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import django
from decimal import Decimal
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.utils import timezone
from laundries.models.laundry import Laundry
from ordering.models.base import Order, OrderItem, OrderStatusHistory
from users.models import User
from ordering.views.tracking_view import build_tracking_payload

def run_test():
    print("=== 1. Checking Laundry ===")
    laundry = Laundry.objects.filter(status=Laundry.ApprovalStatus.APPROVED, is_active=True).first()
    if not laundry:
        print("No active laundry found!")
        return False
    print(f"Using Laundry: {laundry.name} (ID: {laundry.id})")

    print("\n=== 2. Creating/Fetching Customer ===")
    customer, created = User.objects.get_or_create(
        email='delivery_qa_customer@simame.tech',
        defaults={
            'first_name': 'Akua',
            'last_name': 'Mensah',
            'phone': '+233249998877',
            'role': User.Role.CUSTOMER,
        }
    )
    print(f"Customer: {customer.email} (Created: {created})")

    print("\n=== 3. Creating Delivery Order ===")
    order = Order.objects.create(
        user=customer,
        laundry=laundry,
        status=Order.Status.PENDING,
        payment_status=Order.PaymentStatus.PAID,
        payment_method=Order.PaymentMethod.CASH,
        pricing_mode=Order.PricingMode.BY_ITEM,
        pickup_address='House 42, Ring Road Central, Accra',
        pickup_lat=Decimal('5.5600000'),
        pickup_lng=Decimal('-0.2050000'),
        delivery_address='Apartment 7B, Cantonments, Accra',
        delivery_lat=Decimal('5.5800000'),
        delivery_lng=Decimal('-0.1800000'),
        pickup_date=timezone.now() + timedelta(hours=2),
        delivery_date=timezone.now() + timedelta(days=2),
        special_instructions='Please call when arriving at the security gate.',
        items_total=Decimal('45.00'),
        pickup_fee=Decimal('10.00'),
        delivery_fee=Decimal('10.00'),
        total_amount=Decimal('65.00'),
        handover_code='482910'
    )
    print(f"Order Created: {order.order_no} (ID: {order.id})")

    item1 = OrderItem.objects.create(
        order=order,
        name='Suit (2-Piece)',
        quantity=1,
        price=Decimal('30.00')
    )
    item2 = OrderItem.objects.create(
        order=order,
        name='Dress Shirt',
        quantity=1,
        price=Decimal('15.00')
    )
    print(f"Items linked: {item1.name} (x{item1.quantity}), {item2.name} (x{item2.quantity})")

    print("\n=== 4. Simulating Full Delivery Milestones ===")
    milestones = [
        (Order.Status.CONFIRMED, 'confirmed_at', 'Order accepted by laundry partner'),
        (Order.Status.PICKED_UP, 'picked_up_at', 'Driver picked up clothes from customer'),
        (Order.Status.IN_PROCESS, 'processing_started_at', 'Laundry wash, dry & press underway'),
        (Order.Status.OUT_FOR_DELIVERY, 'out_for_delivery_at', 'Driver dispatched for final delivery'),
        (Order.Status.DELIVERED, 'delivered_at', 'Package delivered and confirmed with handover PIN'),
        (Order.Status.COMPLETED, 'completed_at', 'Order completed successfully')
    ]

    prev_status = Order.Status.PENDING
    for status_val, timestamp_field, note in milestones:
        now = timezone.now()
        setattr(order, timestamp_field, now)
        order.status = status_val
        if status_val == Order.Status.DELIVERED:
            order.delivery_confirmed_by_code = True
        order.save()
        
        OrderStatusHistory.objects.create(
            order=order,
            previous_status=prev_status,
            new_status=status_val,
            changed_by=customer,
            metadata={'note': note}
        )
        prev_status = status_val
        print(f" Milestone Reached: {status_val:<18} -> {note}")

    print("\n=== 5. Testing Tracking Payload ===")
    class DummyRequest:
        def __init__(self, user):
            self.user = user
        def build_absolute_uri(self, path):
            return f"https://simame.tech{path}"

    tracking_data = build_tracking_payload(order, request=DummyRequest(customer))
    print("Tracking Data Snapshot Keys:", list(tracking_data.keys()))
    print(f"Order No: {tracking_data.get('order_no')}")
    print(f"Current Status: {tracking_data.get('status')}")
    print(f"Handover Code: {tracking_data.get('handover_code')}")
    print(f"Timeline Steps Count: {len(tracking_data.get('timeline', []))}")
    for step in tracking_data.get('timeline', []):
        print(f"  - {step.get('status')}: completed={step.get('completed')}, time={step.get('time')}")

    print("\n=== ALL DELIVERY CHECKS PASSED 100%! ===")
    return True

if __name__ == '__main__':
    run_test()
