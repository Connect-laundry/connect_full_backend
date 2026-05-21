# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
import logging
import requests
from django.conf import settings
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model
# pyre-ignore[missing-module]
from marketplace.models import Notification, PushDevice
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
        
        send_real_push.delay(str(notification.id))
        
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
    Sends the notification to registered Expo push tokens.
    """
    try:
        notification = Notification.objects.select_related('user').get(id=notification_id)
        if not getattr(settings, 'EXPO_PUSH_ENABLED', False):
            return 0

        devices = PushDevice.objects.filter(user=notification.user, is_active=True)
        messages = [
            {
                "to": device.token,
                "sound": "default",
                "title": notification.title,
                "body": notification.body,
                "data": {
                    "notificationId": str(notification.id),
                    "type": notification.type,
                    "relatedOrder": str(notification.related_order_id) if notification.related_order_id else None,
                },
            }
            for device in devices
            if device.token.startswith('ExponentPushToken[') or device.token.startswith('ExpoPushToken[')
        ]

        if not messages:
            return 0

        response = requests.post(
            "https://exp.host/--/api/v2/push/send",
            json=messages,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        logger.info(
            "Push notifications sent",
            extra={"notification_id": str(notification.id), "count": len(messages)},
        )
        return len(messages)
    except Notification.DoesNotExist:
        pass
    except requests.RequestException as e:
        logger.error("Push delivery failed", extra={"error": summarize_exception(e)})
        raise
    return 0
