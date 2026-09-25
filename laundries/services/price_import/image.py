"""Upload hardening and image normalisation (OWASP file-upload guidance).

upload -> size cap -> magic-byte sniff (never trust name or declared type
alone) -> pixel-count cap *before* decoding -> full decode by Pillow ->
reject animation -> apply EXIF orientation -> drop all metadata -> RGB ->
downscale (never upscale) -> re-encode as a fresh JPEG with a random
server-side name.

Two outputs:
* ``provider_bytes`` for Gemini: long edge <= PRICE_LIST_GEMINI_LONG_EDGE,
  high JPEG quality so small price text survives.
* ``ocr_bytes`` for OCR.space: squeezed under the free plan's 1 MB limit
  without touching the Gemini copy.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import uuid
import warnings
from dataclasses import dataclass

from django.conf import settings
from PIL import Image, ImageFile, ImageOps

from .errors import ImageRejected

# Truncated images must fail, not be silently padded.
ImageFile.LOAD_TRUNCATED_IMAGES = False

ALLOWED_DECLARED_TYPES = {
    'image/jpeg', 'image/jpg', 'image/pjpeg', 'image/png', 'image/webp',
    # Some browsers/clients send a generic type; the sniffed bytes decide.
    'application/octet-stream', '',
}

MIN_DIMENSION = 64
OCR_MIN_LONG_EDGE = 1000


@dataclass
class PreparedImage:
    provider_bytes: bytes
    provider_mime: str
    ocr_bytes: bytes | None
    sha256: str
    width: int
    height: int
    original_bytes: int
    source_format: str
    storage_name: str
    original_filename: str


def sniff_format(head: bytes) -> str | None:
    """Identify an allowed image by magic bytes; ``None`` for anything else."""
    if head.startswith(b'\xff\xd8\xff'):
        return 'JPEG'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'PNG'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'WEBP'
    return None


def _describe_rejected(head: bytes) -> str:
    h = head.lstrip()[:16].lower()
    if head[4:12] in (b'ftypheic', b'ftypheix', b'ftypmif1', b'ftyphevc'):
        return 'HEIC'
    if h.startswith((b'<svg', b'<?xml')):
        return 'SVG'
    if h.startswith((b'<!doctype', b'<html', b'<script')):
        return 'HTML'
    if head.startswith(b'MZ'):
        return 'EXECUTABLE'
    if head.startswith(b'PK\x03\x04'):
        return 'ZIP'
    if head.startswith(b'%PDF'):
        return 'PDF'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'GIF'
    return 'UNKNOWN'


def sanitize_filename(name: str | None) -> str:
    """Display-only copy of the client filename (never used as a path)."""
    if not name:
        return ''
    name = unicodedata.normalize('NFKC', str(name))
    name = re.split(r'[\\/]', name)[-1]                     # strip any path
    name = ''.join(ch for ch in name if ch.isprintable())
    name = re.sub(r'[^\w.\- ()]', '_', name).strip(' .')
    return name[:120]


def prepare_image(upload) -> PreparedImage:
    max_bytes = settings.PRICE_LIST_UPLOAD_MAX_MB * 1024 * 1024
    declared_size = getattr(upload, 'size', None)
    if declared_size is not None and declared_size > max_bytes:
        raise ImageRejected(
            'FILE_TOO_LARGE',
            f'This image is larger than {settings.PRICE_LIST_UPLOAD_MAX_MB} MB. '
            'Please upload a smaller photo.',
            413,
        )
    if hasattr(upload, 'seek'):
        upload.seek(0)
    data = upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageRejected('FILE_TOO_LARGE', 'This image is too large. Please upload a smaller photo.', 413)
    if not data:
        raise ImageRejected('EMPTY_FILE', 'The uploaded file is empty.')

    declared = (getattr(upload, 'content_type', '') or '').lower().split(';')[0].strip()
    fmt = sniff_format(data[:16])
    if fmt is None:
        kind = _describe_rejected(data[:16])
        if kind == 'HEIC':
            raise ImageRejected(
                'UNSUPPORTED_FILE',
                'HEIC photos are not supported yet. Please upload a JPEG or PNG '
                '(or take a screenshot of the photo).',
                415,
            )
        raise ImageRejected('UNSUPPORTED_FILE', 'Please upload a JPEG, PNG or WebP image.', 415)
    if declared not in ALLOWED_DECLARED_TYPES:
        raise ImageRejected('UNSUPPORTED_FILE', 'Please upload a JPEG, PNG or WebP image.', 415)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format != fmt:
                raise ImageRejected('INVALID_IMAGE', 'This image file is damaged or not a real image.')
            width, height = probe.size
            if width * height > settings.PRICE_LIST_MAX_PIXELS:
                raise ImageRejected(
                    'IMAGE_DIMENSIONS_TOO_LARGE',
                    'This image has too many pixels. Please upload a normal photo or screenshot.',
                )
            if min(width, height) < MIN_DIMENSION:
                raise ImageRejected('IMAGE_TOO_SMALL', 'This image is too small to read.')
            if getattr(probe, 'is_animated', False) or getattr(probe, 'n_frames', 1) > 1:
                raise ImageRejected('UNSUPPORTED_FILE', 'Animated images are not supported.', 415)
            probe.load()                     # full decode: catches truncation/corruption
            img = ImageOps.exif_transpose(probe)
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageRejected('IMAGE_DIMENSIONS_TOO_LARGE', 'This image has too many pixels.')
    except Exception:
        raise ImageRejected('INVALID_IMAGE', 'This image file is damaged or not a real image.')

    img = _to_rgb(img)
    img = _downscale(img, settings.PRICE_LIST_GEMINI_LONG_EDGE)
    provider_bytes = _encode_jpeg(img, quality=90)
    ocr_bytes = _encode_for_ocr(img, settings.PRICE_LIST_OCR_MAX_BYTES)

    return PreparedImage(
        provider_bytes=provider_bytes,
        provider_mime='image/jpeg',
        ocr_bytes=ocr_bytes,
        sha256=hashlib.sha256(provider_bytes).hexdigest(),
        width=img.width,
        height=img.height,
        original_bytes=len(data),
        source_format=fmt,
        storage_name=f'{uuid.uuid4().hex}.jpg',
        original_filename=sanitize_filename(getattr(upload, 'name', '')),
    )


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        rgba = img.convert('RGBA')
        background = Image.new('RGB', rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel('A'))
        return background
    if img.mode != 'RGB':
        return img.convert('RGB')
    return img


def _downscale(img: Image.Image, long_edge: int) -> Image.Image:
    if max(img.size) <= long_edge:
        return img                          # never upscale
    img = img.copy()
    img.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    return img


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    # A fresh RGB image carries no EXIF/ICC/XMP; nothing from the upload survives.
    img.save(buf, format='JPEG', quality=quality, optimize=True, subsampling=0 if quality >= 90 else 2)
    return buf.getvalue()


def _encode_for_ocr(img: Image.Image, max_bytes: int) -> bytes | None:
    current = img
    while True:
        for quality in (85, 75, 65):
            data = _encode_jpeg(current, quality)
            if len(data) <= max_bytes:
                return data
        new_long = int(max(current.size) * 0.85)
        if new_long < OCR_MIN_LONG_EDGE:
            return None
        current = _downscale(current, new_long)
