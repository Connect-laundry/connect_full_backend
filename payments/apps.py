from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = 'payments'

    def ready(self):
        from . import checks  # noqa: F401
