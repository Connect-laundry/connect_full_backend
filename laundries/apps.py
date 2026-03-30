from django.apps import AppConfig
import os


class LaundriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "laundries"
    path = os.path.dirname(os.path.abspath(__file__))
