import os
import sys

# Add the current directory to sys.path
sys.path.append(os.getcwd())

import django
from django.conf import settings

try:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    print("Django setup successful!")
except Exception as e:
    import traceback
    print(f"Error during Django setup: {e}")
    traceback.print_exc()
