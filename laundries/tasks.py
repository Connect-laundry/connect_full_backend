# pyre-ignore[missing-module]
import logging
# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.core.cache import cache
# pyre-ignore[missing-module]
from django.db.models import Count, Sum, Q, Avg

from laundries.models.pricing import ScheduledPriceChange, LaundryPricingItem
from laundries.models.laundry import Laundry, OwnerAuditLog
from laundries.models.price_import import PriceListImportJob, PriceListDraftItem
from ordering.models import Order, OrderItem

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def apply_scheduled_pricing_changes(self):
    """
    Periodic task to apply scheduled pricing changes that are due.
    """
    now = timezone.now()
    pending_changes = ScheduledPriceChange.objects.filter(
        is_applied=False,
        effective_at__lte=now
    )
    logger.info(f"Checking for scheduled price changes at {now}. Found {pending_changes.count()} pending.")
    
    for change in pending_changes:
        try:
            with transaction.atomic():
                # Lock the scheduled change
                change = ScheduledPriceChange.objects.select_for_update().get(id=change.id)
                if change.is_applied:
                    continue
                
                laundry = change.laundry
                logger.info(f"Applying scheduled price change {change.id} for laundry {laundry.name}")
                
                # items_data snaps current state before update
                current_items = LaundryPricingItem.objects.filter(laundry=laundry)
                current_snapshot = [
                    {
                        'item_name': item.item_name,
                        'category': item.category,
                        'unit_price': str(item.unit_price),
                        'is_active': item.is_active,
                        'display_order': item.display_order
                    }
                    for item in current_items
                ]
                
                # Apply updates
                for item_data in change.pricing_data:
                    item_name = item_data.get('item_name')
                    if not item_name:
                        continue
                    LaundryPricingItem.objects.update_or_create(
                        laundry=laundry,
                        item_name=item_name,
                        defaults={
                            'category': item_data.get('category', ''),
                            'unit_price': item_data['unit_price'],
                            'is_active': item_data.get('is_active', True),
                            'display_order': item_data.get('display_order', 0)
                        }
                    )
                
                change.is_applied = True
                change.save(update_fields=['is_applied'])
                
                # Create audit log
                OwnerAuditLog.objects.create(
                    laundry=laundry,
                    actor=laundry.owner,
                    action='APPLY_SCHEDULED_PRICING',
                    details={
                        'scheduled_change_id': str(change.id),
                        'effective_at': str(change.effective_at),
                        'snapshot_before': current_snapshot
                    }
                )
                logger.info(f"Successfully applied scheduled price change {change.id}")
        except Exception as e:
            logger.exception(f"Error applying scheduled price change {change.id}: {e}")
            raise e

@shared_task(bind=True, max_retries=3)
def process_ocr_import(self, job_id):
    """
    Asynchronously process an OCR price import job.
    """
    from laundries.services.ocr import get_ocr_provider
    
    logger.info(f"Starting OCR processing task for job {job_id}")
    try:
        job = PriceListImportJob.objects.get(id=job_id)
    except PriceListImportJob.DoesNotExist:
        logger.error(f"PriceListImportJob {job_id} not found.")
        return
    
    if job.status != PriceListImportJob.Status.PROCESSING:
        logger.warning(f"Job {job_id} is not in PROCESSING status. Skipping.")
        return
        
    provider = get_ocr_provider()
    try:
        candidates = provider.extract(job.source_image) or []
        with transaction.atomic():
            for cand in candidates:
                name = (cand.get('item_name') or '').strip()
                if not name:
                    continue
                
                category = (cand.get('category') or '').strip()
                if not category:
                    name_lower = name.lower()
                    if any(k in name_lower for k in ['shirt', 't-shirt', 'top', 'blouse']):
                        category = 'Shirts'
                    elif any(k in name_lower for k in ['trouser', 'pants', 'jeans', 'shorts', 'suit trouser']):
                        category = 'Trousers'
                    elif any(k in name_lower for k in ['dress', 'gown', 'skirt']):
                        category = 'Dresses'
                    elif any(k in name_lower for k in ['suit', 'blazer', 'tuxedo', 'coat', 'jacket']):
                        category = 'Suits'
                    elif any(k in name_lower for k in ['bedding', 'sheet', 'duvet', 'blanket', 'pillow', 'quilt']):
                        category = 'Bedding'
                    elif any(k in name_lower for k in ['curtain', 'drape']):
                        category = 'Curtains'
                    elif any(k in name_lower for k in ['shoe', 'sneaker', 'boot', 'footwear']):
                        category = 'Shoes'
                    elif any(k in name_lower for k in ['household', 'towel', 'rug', 'mat', 'cloth', 'napkin']):
                        category = 'Household'
                    else:
                        category = 'Shirts'
                
                PriceListDraftItem.objects.create(
                    job=job,
                    item_name=name[:120],
                    suggested_price=cand.get('suggested_price'),
                    category=category[:80],
                    confidence=cand.get('confidence', 1.0)
                )
            job.status = PriceListImportJob.Status.READY
            job.save(update_fields=['status', 'updated_at'])
    except Exception as exc:
        logger.exception("OCR processing failed for job %s", job_id)
        job.status = PriceListImportJob.Status.FAILED
        
        # Sanitize error messages to prevent secret or system path leakages
        error_msg = str(exc)
        if isinstance(exc, (ValueError, RuntimeError)) and not any(k in error_msg.lower() for k in ['credential', 'gcp', 'key', 'path', 'token', 'auth']):
            job.error = error_msg[:255]
        else:
            job.error = "The OCR service failed to process the image. Please ensure the image is clear and try again."
            
        job.save(update_fields=['status', 'error', 'updated_at'])


@shared_task
def cache_dashboard_analytics():
    """
    Pre-aggregate and cache dashboard metrics for all active laundries.
    """
    active_laundries = Laundry.objects.filter(is_active=True, status=Laundry.ApprovalStatus.APPROVED)
    logger.info(f"Caching dashboard analytics for {active_laundries.count()} active laundries.")
    
    for laundry in active_laundries:
        try:
            now = timezone.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            base_stats = Order.objects.filter(laundry=laundry).aggregate(
                pending_count=Count('id', filter=Q(status='PENDING')),
                confirmed_count=Count('id', filter=Q(status='CONFIRMED')),
                picked_up_count=Count('id', filter=Q(status='PICKED_UP')),
                delivered_count=Count('id', filter=Q(status='DELIVERED')),
                total_orders=Count('id')
            )

            revenue_stats = Order.objects.filter(
                laundry=laundry,
                status__in=['DELIVERED', 'COMPLETED']
            ).aggregate(
                revenue_today=Sum('total_amount', filter=Q(created_at__gte=today_start), default=0.00),
                revenue_this_month=Sum('total_amount', filter=Q(created_at__gte=month_start), default=0.00),
                average_order_value=Avg('total_amount', default=0.00)
            )

            popular_items_qs = OrderItem.objects.filter(
                order__laundry=laundry
            ).values('name').annotate(
                quantity=Sum('quantity')
            ).order_by('-quantity')[:5]

            most_popular_items = [
                {"name": item['name'], "quantity": item['quantity']}
                for item in popular_items_qs
            ]

            user_orders = Order.objects.filter(laundry=laundry).values('user').annotate(
                order_count=Count('id')
            )
            total_customers = len(user_orders)
            repeat_customers = sum(1 for c in user_orders if c['order_count'] >= 2)
            repeat_customer_rate = (repeat_customers / total_customers) * 100.0 if total_customers > 0 else 0.0

            pending_pickups = Order.objects.filter(laundry=laundry, status='CONFIRMED').count()
            pending_deliveries = Order.objects.filter(laundry=laundry, status__in=['IN_PROCESS', 'OUT_FOR_DELIVERY']).count()

            completed_orders = Order.objects.filter(
                laundry=laundry,
                status__in=['DELIVERED', 'COMPLETED'],
                completed_at__isnull=False
            ).only('created_at', 'completed_at')

            durations = [
                (o.completed_at - o.created_at).total_seconds() / 3600.0
                for o in completed_orders[:1000]
            ]
            average_turnaround_time = sum(durations) / len(durations) if durations else 0.0

            stats = {
                "pending_count": base_stats["pending_count"],
                "confirmed_count": base_stats["confirmed_count"],
                "picked_up_count": base_stats["picked_up_count"],
                "delivered_count": base_stats["delivered_count"],
                "total_orders": base_stats["total_orders"],
                "revenue_today": revenue_stats["revenue_today"],
                "revenue_this_month": revenue_stats["revenue_this_month"],
                "average_order_value": revenue_stats["average_order_value"],
                "most_popular_items": most_popular_items,
                "repeat_customer_rate": repeat_customer_rate,
                "pending_pickups": pending_pickups,
                "pending_deliveries": pending_deliveries,
                "average_turnaround_time": average_turnaround_time,
            }
            
            cache_key = f"dashboard_stats_{laundry.id}"
            cache.set(cache_key, stats, timeout=3600)
        except Exception as e:
            logger.error(f"Failed to cache dashboard analytics for laundry {laundry.id}: {e}")
