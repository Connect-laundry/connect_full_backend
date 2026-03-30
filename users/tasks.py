import logging
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.conf import settings
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def send_password_reset_email(self, email, reset_link):
    """
    Sends a password reset email to the user.
    """
    try:
        subject = "Reset Your Connect Laundry Password"
        from_email = settings.DEFAULT_FROM_EMAIL

        context = {
            "reset_link": reset_link,
            "expiry_hours": settings.PASSWORD_RESET_TOKEN_EXPIRY_HOURS,
            "current_year": timezone.now().year,
        }

        # HTML content
        html_content = render_to_string("users/password_reset_email.html", context)

        # Plain text content fallback
        text_content = (
            f"Hello,\n\n"
            f"We received a request to reset your Connect Laundry password. "
            f"Please click the link below to set a new password:\n\n"
            f"{reset_link}\n\n"
            f"This link will expire in {settings.PASSWORD_RESET_TOKEN_EXPIRY_HOURS} hour(s).\n\n"
            f"If you did not request a password reset, please ignore this email."
        )

        msg = EmailMultiAlternatives(subject, text_content, from_email, [email])
        msg.attach_alternative(html_content, "text/html")
        msg.send()

        logger.info(f"Password reset email sent successfully to {email}")
        return True

    except Exception as e:
        logger.error(f"Error sending password reset email to {email}: {
                str(e)}")
        # Retry the task
        raise self.retry(exc=e, countdown=60)
