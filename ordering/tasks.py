# pyre-ignore[missing-module]
import logging
# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from config.celery_utils import hardened_task
# pyre-ignore[missing-module]
from .models.base import Order

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=5)
@hardened_task(max_retries=5, base_delay=10)
def process_order_confirmation(self, order_id):
    """
    Hardened task to process order confirmation.
    Features:
    - Idempotency using select_for_update
    - Atomic transactions
    - Automatic retries with exponential backoff
    - Structured logging
    """
    logger.info(f"Starting confirmation for order {order_id}")
    
    try:
        with transaction.atomic():
            # Use select_for_update to handle race conditions and ensure idempotency
            order = Order.objects.select_for_update().get(id=order_id)
            
            # Idempotency check: if already processed, skip
            if order.status != Order.Status.PENDING:
                logger.info(f"Order {order_id} already processed (Status: {order.status})")
                return f"Order {order_id} already processed"
            
            # Simulate order processing logic (e.g., verifying payment, notifying laundry)
            # ...
            
            order.status = Order.Status.PICKED_UP # Example transition
            order.save()
            
            logger.info(f"Order {order_id} confirmed and moved to PICKED_UP")
            return f"Order {order_id} confirmed"

    except Order.DoesNotExist:
        logger.error(f"Order {order_id} not found")
        # No retry needed if data doesn't exist
        return f"Order {order_id} not found"
    except Exception as e:
        # Re-raise to trigger hardened_task retry logic
        logger.error(f"Error processing order {order_id}: {str(e)}")
        raise e
