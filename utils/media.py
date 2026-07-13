"""Safe helpers for resolving and writing media/storage files.

Storage backends (Cloudinary in production, local disk in development) can
raise both when *reading* ``FieldFile.url`` and when *writing* an upload, if
credentials are missing, misconfigured (the worst case: set-but-invalid), or
the provider is unreachable. A broken/optional image must never take down an
API response.

This module provides two layers:

* **Read path** — :func:`safe_media_url` and the ``Safe*`` serializer fields
  degrade a broken URL to ``None`` and log a warning.
* **Write path** — :func:`write_media_file` / :func:`save_optional_media` /
  :func:`save_to_storage` centralise the ``try/except`` around a storage write
  so callers either degrade gracefully (optional media) or return a clean 503
  (required media) instead of an unhandled 500.
"""
import logging

# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.core.files.storage import default_storage
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from rest_framework import serializers

logger = logging.getLogger(__name__)


def safe_media_url(file, request=None):
    """Return an absolute (or storage-relative) URL for ``file``, or ``None``.

    ``file`` is any ``FieldFile``-like object (``ImageField``/``FileField``
    value). Returns ``None`` when the field is empty or the storage backend
    cannot produce a URL, logging a warning so misconfiguration is visible
    without crashing the request.
    """
    if not file:
        return None

    try:
        url = file.url
    except Exception as exc:  # storage config/connection errors must degrade
        logger.warning(
            "Media URL unavailable",
            extra={"file": str(file), "error": str(exc)},
        )
        return None

    if request is not None:
        try:
            return request.build_absolute_uri(url)
        except Exception as exc:
            logger.warning(
                "Could not build absolute media URL",
                extra={"file": str(file), "error": str(exc)},
            )
            return url
    return url


class _SafeUrlRepresentationMixin:
    """Read path for file fields that never raises on storage errors.

    DRF's stock ``FileField.to_representation`` only swallows
    ``AttributeError`` from ``value.url``; Cloudinary/storage backends can
    raise other exceptions when unconfigured.
    """

    def to_representation(self, value):
        if not value:
            return None
        if not getattr(self, 'use_url', True):
            return value.name
        return safe_media_url(value, self.context.get('request'))


class SafeFileField(_SafeUrlRepresentationMixin, serializers.FileField):
    pass


class SafeImageField(_SafeUrlRepresentationMixin, serializers.ImageField):
    pass


class SafeMediaModelSerializer(serializers.ModelSerializer):
    """ModelSerializer that maps model file/image fields to the safe variants.

    Auto-built fields keep their model-derived kwargs (validators, blank/null),
    but their read path degrades to ``None`` instead of raising when the
    storage backend is misconfigured or unreachable.
    """

    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        models.FileField: SafeFileField,
        models.ImageField: SafeImageField,
    }


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

# Sentinel distinguishing "key absent" (leave field untouched) from an explicit
# ``None`` (clear the field). Used by callers that support clearing a file.
UNSET = object()


class MediaStorageError(Exception):
    """The media/storage backend failed to persist an upload.

    Callers translate this into a graceful outcome — a 503 for required files,
    or degrading to "no file" for optional ones — instead of leaking an
    unhandled HTTP 500.
    """


def _active_storage_backend() -> str:
    try:
        return settings.STORAGES['default']['BACKEND']
    except Exception:  # pragma: no cover - defensive
        return 'unknown'


def _log_storage_failure(operation, exc, *, request=None, **context):
    """Emit a structured, client-safe log record for a storage write failure.

    Captures request_id, user_id, storage backend, exception type/message and
    the full stack trace (via ``exc_info``) so the failure is diagnosable from
    logs without exposing any internals to the API client. Must be called from
    within an ``except`` block for the traceback to attach.
    """
    request_id = context.pop('request_id', None)
    user_id = context.pop('user_id', None)
    if request is not None:
        request_id = request_id or getattr(request, 'request_id', None)
        user = getattr(request, 'user', None)
        if user_id is None and getattr(user, 'is_authenticated', False):
            user_id = str(getattr(user, 'id', '') or '')
    logger.error(
        "Media storage write failed",
        exc_info=True,
        extra={
            "event": "media_storage_failure",
            "operation": operation,
            "request_id": request_id,
            "user_id": user_id,
            "storage_backend": _active_storage_backend(),
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            **context,
        },
    )


def write_media_file(instance, field_name, file, *, request=None, **context):
    """Persist ``file`` to ``instance.<field_name>`` via its storage backend.

    On any storage failure this logs a structured record and raises
    :class:`MediaStorageError`. Use for **required** files, where the caller
    should surface a 503 (and roll back any accompanying row).
    """
    field_file = getattr(instance, field_name)
    try:
        # ``save=True`` writes to storage *and* persists the model row. The
        # storage write is the operation that fails on bad Cloudinary creds.
        field_file.save(file.name, file, save=True)
    except Exception as exc:
        context.setdefault('model', type(instance).__name__)
        context.setdefault('instance_id', str(getattr(instance, 'pk', '') or ''))
        context.setdefault('field', field_name)
        _log_storage_failure('field_write', exc, request=request, **context)
        raise MediaStorageError(str(exc)) from exc


def save_optional_media(instance, field_name, file, *, request=None, **context) -> bool:
    """Best-effort attach of an **optional** upload to a model instance.

    Returns ``True`` when the file was stored, ``False`` when it was absent or
    the storage backend failed. Never raises: a missing or broken optional file
    must not break the request. On failure the field is left empty and the row
    keeps all of its other data.
    """
    if not file:
        return False
    try:
        write_media_file(instance, field_name, file, request=request, **context)
        return True
    except MediaStorageError:
        return False


def save_to_storage(path, file, *, request=None, **context):
    """Write ``file`` to ``path`` on the default storage backend.

    Returns ``(saved_path, url)``. Raises :class:`MediaStorageError` on failure.
    A lower-level counterpart to :func:`write_media_file` for views that store
    to a path directly rather than through a model field.
    """
    try:
        saved_path = default_storage.save(path, file)
        return saved_path, default_storage.url(saved_path)
    except Exception as exc:
        context.setdefault('path', path)
        _log_storage_failure('storage_save', exc, request=request, **context)
        raise MediaStorageError(str(exc)) from exc


class OptionalMediaWriteMixin:
    """Serializer mixin: defer optional file/image writes until after the row
    is saved, degrading to "no file" on storage failure instead of raising.

    Without this, a ``ModelSerializer`` writes the upload to storage *during*
    ``Model.save()``; a misconfigured/unreachable backend then raises and DRF
    surfaces an unhandled HTTP 500. Declare the optional fields on ``Meta``::

        class Meta:
            optional_media_fields = ['avatar']

    Only the write path changes; reads still use the ``Safe*`` field mapping.
    Combine with :class:`SafeMediaModelSerializer`, e.g.
    ``class FooSerializer(OptionalMediaWriteMixin, SafeMediaModelSerializer)``.
    """

    def _optional_media_field_names(self):
        return list(getattr(self.Meta, 'optional_media_fields', []) or [])

    def _extract_optional_media(self, validated_data):
        return {
            name: validated_data.pop(name)
            for name in self._optional_media_field_names()
            if name in validated_data
        }

    def _apply_optional_media(self, instance, media):
        if not media:
            return
        request = self.context.get('request')
        for field_name, file in media.items():
            if file in (None, ''):
                # Explicit clear — a plain DB update, not a storage write.
                setattr(instance, field_name, None)
                instance.save(update_fields=[field_name])
            else:
                save_optional_media(instance, field_name, file, request=request)

    def create(self, validated_data):
        media = self._extract_optional_media(validated_data)
        instance = super().create(validated_data)
        self._apply_optional_media(instance, media)
        return instance

    def update(self, instance, validated_data):
        media = self._extract_optional_media(validated_data)
        instance = super().update(instance, validated_data)
        self._apply_optional_media(instance, media)
        return instance
