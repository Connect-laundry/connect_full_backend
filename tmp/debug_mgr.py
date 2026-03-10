import sys
import traceback
import os

try:
    sys.path.append(os.getcwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(["manage.py", "seed_booking"])
except Exception as e:
    with open("tmp/traceback_dump.txt", "w") as f:
        traceback.print_exc(file=f)
