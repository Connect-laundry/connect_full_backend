import sys
import os
import traceback
sys.path.append(os.getcwd())

try:
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
except Exception as e:
    with open("err.txt", "w") as f:
        traceback.print_exc(file=f)
