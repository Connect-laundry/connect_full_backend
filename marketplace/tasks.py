# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
import logging
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model
# pyre-ignore[missing-module]
from marketplace.models import Notification
# pyre-ignore[missing-module]
from django.core.exceptions import ObjectDoesNotExist
from config.redaction import summarize_exception

User = get_user_model()
logger = logging.getLogger(__name__)

@shared_task(
    name="marketplace.tasks.create_notification",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 5}
)
def create_notification(self, user_id, title, body, notification_type='SYSTEM', related_order_id=None):
    """
    Asynchronously creates a notification record in the database.
    This can be extended to trigger real push notifications (FCM/OneSignal).
    """
    try:
        user = User.objects.get(id=user_id)
        notification = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            type=notification_type,
            related_order_id=related_order_id
        )
        
        # Placeholder for real Push Service integration
        # send_real_push.delay(notification.id)
        
        logger.info(
            "Notification created",
            extra={"user_id": str(user.id), "notification_id": str(notification.id)},
        )
        return str(notification.id)
    except User.DoesNotExist:
        logger.error("Failed to create notification: user not found", extra={"user_id": str(user_id)})
    except Exception as e:
        logger.error("Error creating notification", extra={"error": summarize_exception(e)})
    return None

@shared_task(
    name="marketplace.tasks.send_real_push",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 7}
)
def send_real_push(self, notification_id):
    """
    Integration hook for external push services like Firebase or OneSignal.
    """
    try:
        notification = Notification.objects.get(id=notification_id)
        logger.info("Push delivery hook invoked", extra={"notification_id": str(notification.id)})
    except Notification.DoesNotExist:
        pass
