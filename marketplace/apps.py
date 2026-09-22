# pyre-ignore[missing-module]
from django.apps import AppConfig


class MarketplaceConfig(AppConfig):
    name = 'marketplace'

    def ready(self):
        import marketplace.signals
        from django.core.signals import request_finished
        from marketplace.push_sweep import maybe_sweep_after_request
        request_finished.connect(maybe_sweep_after_request, dispatch_uid='push-inprocess-sweep')
