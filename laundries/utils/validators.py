import os
import logging

# pyre-ignore[missing-module]
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_IMAGE_UPLOAD_SIZE = 2 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
ALLOWED_IMAGE_MIME_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_PIL_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def _has_uploaded_file_interface(value):
    return hasattr(value, 'read') and hasattr(value, 'seek')


def _get_file_for_validation(value):
    if _has_uploaded_file_interface(value):
        return value

    nested_file = getattr(value, 'file', None)
    if _has_uploaded_file_interface(nested_file):
        return nested_file

    return None


def _is_already_persisted_file(value):
    if isinstance(value, str):
        return True

    name = str(getattr(value, 'name', '') or '')
    if name.startswith(('http://', 'https://')):
        return True

    if getattr(value, '_committed', False) and not hasattr(value, 'content_type'):
        return True

    return False


def _safe_seek(file_obj, position=0):
    try:
        file_obj.seek(position)
    except (AttributeError, OSError, ValueError):
        return


def validate_file_upload(value):
    """
    Validate a newly uploaded image before storage persistence.

    Cloudinary returns extensionless delivery URLs/paths after persistence; those
    are not uploads and must not be revalidated as filenames.
    """
    if value in (None, '') or _is_already_persisted_file(value):
        return

    request_id = getattr(value, 'request_id', None)
    uploaded_file = _get_file_for_validation(value)
    if uploaded_file is None:
        logger.warning(
            "Image validation skipped for non-upload value",
            extra={"request_id": request_id, "value_type": type(value).__name__},
        )
        return

    size = getattr(value, 'size', None) or getattr(uploaded_file, 'size', None)
    if size and size > MAX_IMAGE_UPLOAD_SIZE:
        raise ValidationError('File size too large. Size should not exceed 2 MB.')

    ext = os.path.splitext(str(getattr(value, 'name', '') or ''))[1].lower()
    if ext and ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError('Unsupported file extension.')

    content_type = str(getattr(value, 'content_type', '') or '').lower()
    if content_type and content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValidationError('Invalid file type.')

    _safe_seek(uploaded_file, 0)
    try:
        with Image.open(uploaded_file) as image:
            image.verify()
            detected_format = image.format
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info(
            "Rejected invalid image upload",
            extra={"request_id": request_id, "filename": getattr(value, 'name', None)},
        )
        raise ValidationError('Invalid image file.') from exc
    finally:
        _safe_seek(uploaded_file, 0)

    if detected_format not in ALLOWED_PIL_FORMATS:
        raise ValidationError('Unsupported image format.')
