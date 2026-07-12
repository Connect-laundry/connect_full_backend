"""Safe Celery task dispatch.

``task.delay()`` raises (kombu ``OperationalError`` among others) when the
broker is unreachable. A broker outage must never turn a customer request
into an HTTP 500 — use :func:`safe_task_delay` in every request path.
"""
import logging

logger = logging.getLogger(__name__)


def safe_task_delay(task, *args, fallback_sync=False, **kwargs):
    """Queue ``task`` for async execution, degrading gracefully.

    Returns ``True`` if the task was queued (or, with ``fallback_sync=True``,
    executed inline after a broker failure), ``False`` if it could not run at
    all. Never raises.

    Use ``fallback_sync=True`` only for cheap, user-critical work (e.g. a
    password-reset email); heavy jobs should return ``False`` and let the
    caller record a controlled failure instead.
    """
    task_name = getattr(task, 'name', repr(task))
    try:
        task.delay(*args, **kwargs)
        return True
    except Exception as exc:
        logger.warning(
            "Celery broker unavailable; could not queue task",
            extra={"task": task_name, "error": str(exc)},
        )

    if fallback_sync:
        try:
            task.apply(args=args, kwargs=kwargs)
            logger.info(
                "Task executed synchronously after broker failure",
                extra={"task": task_name},
            )
            return True
        except Exception as sync_exc:
            logger.error(
                "Synchronous fallback failed",
                extra={"task": task_name, "error": str(sync_exc)},
            )

    return False
