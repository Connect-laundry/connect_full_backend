import logging
import time
import functools
# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from sentry_sdk import capture_exception, push_scope

logger = logging.getLogger(__name__)

def hardened_task(max_retries=3, base_delay=5):
    """
    Decorator for Celery tasks to provide:
    - Exponential backoff retries
    - Structured JSON logging
    - Sentry integration
    - Persistence of permanent failures
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            start_time = time.time()
            task_id = self.request.id
            task_name = self.name
            retry_count = self.request.retries

            log_extra = {
                'task_id': task_id,
                'task_name': task_name,
                'retry_count': retry_count
            }

            try:
                result = func(self, *args, **kwargs)
                
                execution_time = time.time() - start_time
                log_extra['execution_time'] = execution_time
                logger.info(f"Task {task_name} completed successfully", extra=log_extra)
                
                return result

            except Exception as exc:
                # Capture in Sentry with context
                with push_scope() as scope:
                    scope.set_tag("task_id", task_id)
                    scope.set_tag("task_name", task_name)
                    scope.set_extra("args", args)
                    scope.set_extra("kwargs", kwargs)
                    capture_exception(exc)

                if retry_count < max_retries:
                    # Exponential backoff: base_delay * (2 ^ retry_count)
                    countdown = base_delay * (2 ** retry_count)
                    logger.warning(
                        f"Task {task_name} failed. Retrying in {countdown}s...",
                        extra=log_extra
                    )
                    raise self.retry(exc=exc, countdown=countdown)

                # Permanent failure - persist to DB
                logger.error(
                    f"Task {task_name} failed permanently after {max_retries} retries",
                    extra=log_extra,
                    exc_info=True
                )
                
                # Import here to avoid circular dependencies
                # pyre-ignore[import]
                from marketplace.models import FailedTask
                import traceback
                
                try:
                    with transaction.atomic():
                        FailedTask.objects.create(
                            task_id=task_id,
                            task_name=task_name,
                            args=list(args),
                            kwargs=kwargs,
                            exception=str(exc),
                            stack_trace=traceback.format_exc(),
                            retry_count=retry_count
                        )
                except Exception as db_err:
                    logger.critical(f"Could not persist FailedTask: {db_err}")
                
                raise exc

        return wrapper
    return decorator
