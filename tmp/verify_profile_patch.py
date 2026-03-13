import os
import sys
import django

# Add current directory to path
sys.path.append(os.getcwd())

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DEBUG'] = 'True' # Force debug mode to avoid SSL redirects
django.setup()

from django.conf import settings
settings.SECURE_SSL_REDIRECT = False # Ensure no SSL redirect

from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
import io

from users.models import User

def test_profile_persistence():
    # 1. Setup user
    email = "test_persistence@example.com"
    User.objects.filter(email=email).delete()
    user = User.objects.create_user(email=email, phone="00000000", password="password123")
    
    client = APIClient()
    client.force_authenticate(user=user)
    
    # 2. Check initial profile
    print("\n--- Initial Profile ---")
    response = client.get('/api/v1/auth/me/')
    print(f"GET Status: {response.status_code}")
    print(f"GET Data: {response.data}")
    
    # 3. Patch with avatar
    print("\n--- Patching Profile ---")
    
    # Generate valid 1x1 pixel image using Pillow
    file_io = io.BytesIO()
    image = Image.new('RGB', (1, 1), color='black')
    image.save(file_io, 'JPEG')
    file_io.seek(0)
    
    avatar_file = SimpleUploadedFile("avatar.jpg", file_io.getvalue(), content_type="image/jpeg")
    
    patch_data = {
        "first_name": "Updated",
        "last_name": "User",
        "avatar": avatar_file
    }
    
    response = client.patch('/api/v1/auth/me/', data=patch_data, format='multipart')
    print(f"PATCH Status: {response.status_code}")
    print(f"PATCH Data: {response.data}")
    
    # 4. Refresh from DB and check GET
    print("\n--- GET Profile after Patch ---")
    response = client.get('/api/v1/auth/me/')
    print(f"GET Status: {response.status_code}")
    print(f"GET Data: {response.data}")
    
    user.refresh_from_db()
    print(f"\nFinal DB Avatar: {user.avatar}")
    
    if user.avatar:
        print("SUCCESS: Avatar persisted in DB.")
    else:
        print("FAILURE: Avatar not found in DB.")

if __name__ == "__main__":
    test_profile_persistence()
