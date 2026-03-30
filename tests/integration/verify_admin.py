from users.models import User
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


try:
    u = User.objects.get(email="admin@connect.com")
    print(f"User found: {u.email}")
    print(f'Password Valid: {u.check_password("admin123")}')
    print(f"Is Staff: {u.is_staff}")
    print(f"Is Superuser: {u.is_superuser}")
    print(f"Role: {u.role}")
except User.DoesNotExist:
    print("User admin@connect.com not found")
except Exception as e:
    print(f"Error: {str(e)}")
