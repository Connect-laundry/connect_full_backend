import sys
import os
import traceback

try:
    sys.path.append(os.getcwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(["manage.py", "test"])
except Exception as e:
    with open("tmp/test_traceback.txt", "w") as f:
        traceback.print_exc(file=f)
