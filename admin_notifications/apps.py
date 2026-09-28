from django.apps import AppConfig


class AdminNotificationsConfig(AppConfig):
    name = 'admin_notifications'
    verbose_name = 'Admin order notifications'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from django.core import checks
        from django.core.signals import request_finished

        from . import signals  # noqa: F401  (connects order receivers)
        from .checks import admin_notification_config_check
        from .services.kick import maybe_sweep_after_request

        checks.register(admin_notification_config_check)
        request_finished.connect(maybe_sweep_after_request, dispatch_uid='admin-notification-sweep')
