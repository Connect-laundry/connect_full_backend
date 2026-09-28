"""Low-latency dispatch without Celery or Redis.

Production runs without a broker, so alerts are pushed out two ways, both
backed by the durable outbox:

- ``kick_dispatch`` runs after the order transaction commits and hands a
  dispatch pass to a single background worker thread. A burst of 50 bookings
  queues at most one extra pass: kicks that arrive while a pass is waiting
  are coalesced into it. Thread count is bounded (one per process).
- ``maybe_sweep_after_request`` (a ``request_finished`` receiver, same design
  as marketplace/push_sweep.py) kicks a pass at most every
  ADMIN_NOTIFICATION_SWEEP_SECONDS so retries fire even when no new orders
  arrive. Render's health checks alone keep this ticking.

``manage.py dispatch_admin_notifications`` on a cron is the third, optional
safety net. All three claim rows with SKIP LOCKED, so they never double-send.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import connection

from .. import conf

logger = logging.getLogger('admin_notifications')

_lock = threading.Lock()
_executor = None
_pass_queued = False
_next_sweep_at = 0.0


def _get_executor():
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='admin-notify')
    return _executor


def _run_pass():
    global _pass_queued
    with _lock:
        _pass_queued = False
    try:
        from .dispatcher import dispatch_due
        dispatch_due()
    except Exception as exc:
        logger.warning('Admin notification background dispatch failed', exc_info=True,
                       extra={'event': 'admin_notification.dispatch_error'})
        from .metrics import alert
        alert('admin_notification.dispatch_error', 'Admin notification dispatcher crashed',
              error_class=type(exc).__name__)
    finally:
        connection.close()


def kick_dispatch():
    """Schedule one dispatch pass in the background (post-commit hook)."""
    global _pass_queued
    if not conf.dispatch_in_thread() or not conf.notifications_enabled():
        return
    with _lock:
        if _pass_queued:
            return
        _pass_queued = True
        executor = _get_executor()
    try:
        executor.submit(_run_pass)
    except RuntimeError:  # interpreter shutting down; the sweep/cron picks it up
        with _lock:
            _pass_queued = False


def _sweep_due() -> bool:
    global _next_sweep_at
    with _lock:
        now = time.monotonic()
        if now < _next_sweep_at:
            return False
        _next_sweep_at = now + conf.sweep_seconds()
        return True


def maybe_sweep_after_request(sender=None, **kwargs):
    if not conf.sweep_enabled() or not conf.notifications_enabled():
        return
    if _sweep_due():
        kick_dispatch()
