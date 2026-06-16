from django.dispatch import receiver
from ordering.services.order_state_machine import order_status_changed
from .models import TrackingLog

@receiver(order_status_changed)
def handle_order_status_changed(sender, order, from_status, to_status, user, metadata, **kwargs):
    # Determine location name
    location = order.pickup_address
    if to_status in ['OUT_FOR_DELIVERY', 'DELIVERED', 'COMPLETED']:
        location = order.delivery_address or order.pickup_address

    description_map = {
        'PENDING': "Order submitted and pending store confirmation.",
        'CONFIRMED': "Order confirmed by the store.",
        'REJECTED': "Order was rejected by the store.",
        'PICKED_UP': "Laundry picked up by rider and in transit to store.",
        'IN_PROCESS': "Laundry is being washed and processed.",
        'OUT_FOR_DELIVERY': "Laundry is clean and out for delivery.",
        'DELIVERED': "Laundry delivered to destination.",
        'COMPLETED': "Order completed.",
        'CANCELLED': "Order was cancelled.",
    }
    
    desc = description_map.get(to_status, f"Order status updated to {to_status}.")
    if to_status == 'CANCELLED' and getattr(order, 'cancellation_reason', None):
        desc += f" Reason: {order.cancellation_reason}"
    elif to_status == 'REJECTED' and getattr(order, 'rejection_reason', None):
        desc += f" Reason: {order.rejection_reason}"

    # Default to laundry coordinates if driver isn't tracking yet
    lat = order.laundry.latitude if order.laundry else None
    lng = order.laundry.longitude if order.laundry else None

    TrackingLog.objects.create(
        order=order,
        status=to_status.lower(),  # use lowercase for frontend normalization
        description=desc,
        location_name=location,
        latitude=lat,
        longitude=lng
    )
