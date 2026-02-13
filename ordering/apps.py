# pyre-ignore[missing-module]
from django.apps import AppConfig


class OrderingConfig(AppConfig):
    name = 'ordering'

    def ready(self):
        # pyre-ignore[import]
        import ordering.signals
