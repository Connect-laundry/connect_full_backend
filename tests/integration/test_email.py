from django.conf import settings
from django.core.mail import send_mail
import os
import sys
import django

# Add current directory to sys.path
sys.path.append(os.getcwd())


# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()


def test_email():
    print(f"Testing email with:")
    print(f"HOST: {settings.EMAIL_HOST}")
    print(f"PORT: {settings.EMAIL_PORT}")
    print(f"USER: {settings.EMAIL_HOST_USER}")
    print(f"TLS: {settings.EMAIL_USE_TLS}")
    print(f"FROM: {settings.DEFAULT_FROM_EMAIL}")

    try:
        send_mail(
            'Connect Laundry - Test Email',
            'This is a test email from the diagnostic script.',
            settings.DEFAULT_FROM_EMAIL,
            [settings.EMAIL_HOST_USER],  # Send to self
            fail_silently=False,
        )
        print("\nSUCCESS! Email sent successfully.")
    except Exception as e:
        print(f"\nFAILED to send email.")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")


if __name__ == "__main__":
    test_email()
