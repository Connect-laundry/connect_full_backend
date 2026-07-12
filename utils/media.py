"""Safe helpers for resolving media/storage URLs.

Storage backends (Cloudinary in production, local disk in development) can
raise on ``FieldFile.url`` when credentials are missing, misconfigured, or the
provider is unreachable. A broken/optional image must never take down an API
response — these helpers degrade to ``None`` and log a structured warning
instead.
"""
import logging

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
