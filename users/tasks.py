# pyre-ignore[missing-module]
from celery import shared_task
# pyre-ignore[missing-module]
from django.core.mail import send_mail
# pyre-ignore[missing-module]
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def send_otp_email(self, email, otp):
    try:
        subject = 'Your Verification Code'
        message = f'Your 6-digit verification code is: {otp}\nIt expires in 5 minutes.'
        from_email = settings.EMAIL_HOST_USER
        
        send_mail(subject, message, from_email, [email])
        logger.info(f"OTP email sent to {email}: {otp}")
        return True
    except Exception as exc:
        logger.error(f"Error sending OTP email to {email}: {exc}")
        raise self.retry(exc=exc, countdown=60)

@shared_task(bind=True, max_retries=3)
def send_otp_sms(self, phone, otp):
    # Placeholder for SMS provider logic (e.g., Twilio)
    try:
        logger.info(f"OTP SMS (Simulated) sent to {phone}: {otp}")
        return True
    except Exception as exc:
        logger.error(f"Error sending OTP SMS to {phone}: {exc}")
        raise self.retry(exc=exc, countdown=60)
