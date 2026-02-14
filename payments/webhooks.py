import hmac
import hashlib
import json
import logging
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from payments.models import Payment
from ordering.models import Order

logger = logging.getLogger(__name__)

from django.db import transaction
from django.utils import timezone
from .models import Payment, WebhookEvent
import hmac
import hashlib
import json
import logging

logger = logging.getLogger(__name__)

@csrf_exempt
@require_POST
def paystack_webhook(request):
    """
    POST /api/v1/payments/webhook/
    Secure webhook handler for Paystack events.
    """
    secret = settings.PAYSTACK_SECRET_KEY
    payload = request.body
    signature = request.headers.get('x-paystack-signature')

    if not signature:
        logger.warning("Webhook received without signature.")
        return HttpResponse(status=401)

    # 1. Verify x-paystack-signature using HMAC SHA512
    hash_computed = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha512
    ).hexdigest()

    if hash_computed != signature:
        logger.warning(f"Invalid Paystack webhook signature detected.")
        return HttpResponse(status=401)

    # 2. Parse event data
    try:
        event_data = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    event_id = event_data.get('data', {}).get('id')
    event_type = event_data.get('event')
    
    # 3. Webhook Replay Protection
    if event_id:
        if WebhookEvent.objects.filter(event_id=event_id).exists():
            logger.info(f"Duplicate webhook event ignored: {event_id}")
            return HttpResponse(status=200)
        
        WebhookEvent.objects.create(event_id=event_id)

    # 4. Only handle charge.success
    if event_type == 'charge.success':
        data = event_data.get('data', {})
        reference = data.get('reference')
        
        if not reference:
            return HttpResponse(status=400)

        # 5. Use transaction.atomic() and select_for_update()
        try:
            with transaction.atomic():
                payment = Payment.objects.select_for_update().filter(transaction_reference=reference).first()
                
                if not payment:
                    logger.error(f"Payment record not found for webhook reference: {reference}")
                    return HttpResponse(status=200) # Safe exit

                # 6. Idempotency Check
                if payment.status == Payment.Status.SUCCESS:
                    logger.info(f"Payment {reference} already marked as SUCCESS. Skipping.")
                    return HttpResponse(status=200)

                # 7. Update Status
                payment.status = Payment.Status.SUCCESS
                payment.raw_response = event_data # Store raw payload
                payment.paid_at = timezone.now()
                payment.save()
                
                # Update order
                order = payment.order
                order.status = Order.Status.CONFIRMED
                order.save()
                
                logger.info(f"Webhook Success: Reference {reference} confirmed.")
        except Exception as e:
            logger.error(f"Error processing webhook for reference {reference}: {e}")
            # We return 500 to let Paystack retry if it's a transient failure
            return HttpResponse(status=500)

    return HttpResponse(status=200)
