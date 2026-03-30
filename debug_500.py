import traceback
from rest_framework.test import APIRequestFactory, force_authenticate
from users.models import User
from laundries.models.laundry import Laundry
from laundries.views.admin_views import AdminLaundryViewSet
import os
import django
import logging

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()


def debug_approve():
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        laundry = Laundry.objects.last()  # Latest one from test
        if not laundry:
            print("No laundry found")
            return

        print(f"Testing for Laundry ID: {laundry.id}")

        try:
            admin_user = User.objects.get(email='testadmin100@example.com')
        except User.DoesNotExist:
            print("Admin user not found")
            return

        factory = APIRequestFactory()
        request = factory.patch(
            f'/api/v1/laundries/admin/laundries/{laundry.id}/approve/')
        force_authenticate(request, user=admin_user)

        view = AdminLaundryViewSet.as_view({'patch': 'approve'})
        response = view(request, pk=str(laundry.id))

        print(f"Status: {response.status_code}")
        print(f"Data: {response.data}")
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    debug_approve()
