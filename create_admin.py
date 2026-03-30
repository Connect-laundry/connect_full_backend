import os
import django
from django.contrib.auth import get_user_model

# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from scripts.seed_booking_data import seed_booking_data  # noqa: E402

User = get_user_model()
if not User.objects.filter(email="testadmin100@example.com").exists():
    User.objects.create_superuser(
        "testadmin100@example.com", "01234567890", "testpassword123"
    )

print("Running initial data seeding...")
seed_booking_data()
