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


@shared_task(bind=True, max_retries=5, autoretry_for=(Exception,), retry_backoff=30, retry_backoff_max=600)
def send_admin_new_laundry_email(self, laundry_id, resubmission=False):
    """Email platform admins that a laundry is waiting for approval.

    Retries with backoff on any failure (SMTP outage, transient DNS, ...).
    Recipients come from settings.LAUNDRY_APPROVAL_NOTIFY_EMAILS.
    """
    from django.conf import settings
    from marketplace.services.providers import get_email_provider, get_sms_provider

    recipients = getattr(settings, 'LAUNDRY_APPROVAL_NOTIFY_EMAILS', [])
    if not recipients:
        logger.info("No LAUNDRY_APPROVAL_NOTIFY_EMAILS configured; skipping admin email.")
        return

    try:
        laundry = Laundry.objects.select_related('owner').get(id=laundry_id)
    except Laundry.DoesNotExist:
        logger.warning("Laundry %s no longer exists; skipping approval email.", laundry_id)
        return

    base = getattr(settings, 'ADMIN_BASE_URL', '').rstrip('/')
    change_url = f"{base}/admin/laundries/laundry/{laundry.id}/change/"
    approve_url = f"{base}/admin/laundries/laundry/{laundry.id}/approve/"
    reject_url = f"{base}/admin/laundries/laundry/{laundry.id}/reject/"
    queue_url = f"{base}/admin/laundries/laundry/?status__exact=PENDING"

    owner = laundry.owner
    owner_name = (owner.get_full_name() or owner.email) if owner else 'Unknown'
    owner_email = getattr(owner, 'email', '') or '—'
    owner_phone = getattr(owner, 'phone_number', '') or laundry.phone_number or '—'
    submitted = laundry.submitted_at or laundry.created_at

    subject = (
        f"Laundry Resubmitted For Approval: {laundry.name}" if resubmission
        else f"New Laundry Waiting For Approval: {laundry.name}"
    )
    text = (
        f"{'A laundry was resubmitted' if resubmission else 'A new laundry was submitted'} "
        f"and is waiting for your approval.\n\n"
        f"Laundry:   {laundry.name}\n"
        f"Owner:     {owner_name}\n"
        f"Phone:     {owner_phone}\n"
        f"Email:     {owner_email}\n"
        f"City:      {laundry.city}\n"
        f"Address:   {laundry.address}\n"
        f"Submitted: {submitted:%Y-%m-%d %H:%M}\n\n"
        f"Review:  {change_url}\n"
        f"Approve: {approve_url}\n"
        f"Reject:  {reject_url}\n"
        f"Queue:   {queue_url}\n"
    )

    def _btn(url, label, color):
        return (
            f'<a href="{url}" style="display:inline-block;margin:4px 6px 4px 0;'
            f'padding:10px 18px;border-radius:8px;background:{color};color:#ffffff;'
            f'text-decoration:none;font-weight:600;font-family:sans-serif;">{label}</a>'
        )

    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;color:#111827;">
      <h2 style="color:#7e22ce;">{'Laundry resubmitted for approval' if resubmission else 'New laundry waiting for approval'}</h2>
      <table style="border-collapse:collapse;width:100%;font-size:14px;">
        <tr><td style="padding:6px 12px 6px 0;color:#6b7280;">Laundry</td><td style="padding:6px 0;"><strong>{laundry.name}</strong></td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#6b7280;">Owner</td><td style="padding:6px 0;">{owner_name}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#6b7280;">Phone</td><td style="padding:6px 0;">{owner_phone}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#6b7280;">Email</td><td style="padding:6px 0;">{owner_email}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#6b7280;">City</td><td style="padding:6px 0;">{laundry.city}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#6b7280;">Address</td><td style="padding:6px 0;">{laundry.address}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#6b7280;">Submitted</td><td style="padding:6px 0;">{submitted:%Y-%m-%d %H:%M}</td></tr>
      </table>
      <div style="margin:20px 0;">
        {_btn(change_url, 'Open in Admin', '#7e22ce')}
        {_btn(approve_url, 'Approve', '#16a34a')}
        {_btn(reject_url, 'Reject', '#dc2626')}
      </div>
      <p style="font-size:12px;color:#6b7280;">You can also work through the
      <a href="{queue_url}" style="color:#7e22ce;">pending approval queue</a>.</p>
    </div>
    """

    get_email_provider().send(to=recipients, subject=subject, text=text, html=html)
    logger.info("Admin approval email sent", extra={"laundry_id": str(laundry.id)})

    # Optional SMS channel — no-op until an SMS provider is configured.
    sms_numbers = getattr(settings, 'LAUNDRY_APPROVAL_NOTIFY_SMS', [])
    if sms_numbers:
        get_sms_provider().send(
            to=sms_numbers,
            body=f"Connect: '{laundry.name}' is waiting for laundry approval.",
        )


@shared_task(bind=True, max_retries=5, autoretry_for=(Exception,), retry_backoff=30, retry_backoff_max=600)
def send_owner_status_email(self, laundry_id, new_status, reason=''):
    """Email the laundry owner about an approval decision. Retries on failure."""
    from django.conf import settings
    from marketplace.services.providers import get_email_provider

    try:
        laundry = Laundry.objects.select_related('owner').get(id=laundry_id)
    except Laundry.DoesNotExist:
        logger.warning("Laundry %s no longer exists; skipping owner email.", laundry_id)
        return

    owner_email = getattr(laundry.owner, 'email', None)
    if not owner_email:
        return

    content = {
        'APPROVED': (
            f"Your laundry is live! 🎉",
            f"Great news — '{laundry.name}' has been approved and is now visible to "
            f"customers on Connect Laundry.",
        ),
        'REJECTED': (
            f"Your laundry submission was not approved",
            f"Unfortunately '{laundry.name}' was not approved."
            + (f"\n\nReason: {reason}" if reason else ""),
        ),
        'CHANGES_REQUESTED': (
            f"Action needed on your laundry submission",
            f"We reviewed '{laundry.name}' and need a few changes before it can go live."
            + (f"\n\nWhat to fix: {reason}" if reason else "")
            + "\n\nUpdate your laundry in the app — it will automatically be resubmitted for review.",
        ),
        'SUSPENDED': (
            f"Your laundry has been suspended",
            f"'{laundry.name}' has been suspended and is no longer visible to customers."
            + (f"\n\nReason: {reason}" if reason else ""),
        ),
    }
    if new_status not in content:
        return
    subject, body = content[new_status]

    get_email_provider().send(
        to=[owner_email],
        subject=subject,
        text=f"Hello,\n\n{body}\n\n— The Connect Laundry Team",
    )
    logger.info(
        "Owner status email sent",
        extra={"laundry_id": str(laundry.id), "status": new_status},
    )


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
