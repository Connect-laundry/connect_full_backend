# pyre-ignore[missing-module]
from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = 'users'

    def ready(self):
        from . import checks  # noqa: F401
        from .auth import schema  # noqa: F401
