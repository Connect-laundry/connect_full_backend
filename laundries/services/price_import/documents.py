"""Bounded, sanitized multi-page uploads. Raw documents never become public media.

PDFium calls are serialized within a process because PDFium is not thread safe.
No PDF scripts, external links or forms are executed; only page pixels enter AI.
"""
from __future__ import annotations

import hashlib
import io
import math
import threading
import uuid
import zipfile
from dataclasses import replace

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from .errors import ImageRejected
from .image import PreparedImage, prepare_image, sanitize_filename

MAX_PAGES = 5
_PDF_LOCK = threading.Lock()


def prepare_documents(uploads) -> PreparedImage:
    uploads = list(uploads)
    if not uploads or len(uploads) > MAX_PAGES:
        raise ImageRejected('PAGE_LIMIT', f'Choose between 1 and {MAX_PAGES} images or PDF pages.')
    max_bytes = settings.PRICE_LIST_UPLOAD_MAX_MB * 1024 * 1024
    if sum(getattr(u, 'size', 0) for u in uploads) > max_bytes:
        raise ImageRejected('FILE_TOO_LARGE', f'Keep all files together under {settings.PRICE_LIST_UPLOAD_MAX_MB} MB.', 413)
    pages: list[PreparedImage] = []
    total_bytes = 0
    for upload in uploads:
        upload.seek(0)
        data = upload.read(max_bytes + 1)
        total_bytes += len(data)
        if total_bytes > max_bytes:
            raise ImageRejected('FILE_TOO_LARGE', 'The combined upload is too large.', 413)
        if data.startswith(b'%PDF-'):
            declared = (getattr(upload, 'content_type', '') or '').split(';')[0].lower()
            if declared not in ('application/pdf', 'application/octet-stream', ''):
                raise ImageRejected('UNSUPPORTED_FILE', 'The file type does not match this PDF.', 415)
            pages.extend(_pdf_pages(data, MAX_PAGES - len(pages)))
        else:
            upload.seek(0)
            pages.append(prepare_image(upload))
        if len(pages) > MAX_PAGES:
            raise ImageRejected('PAGE_LIMIT', f'Upload at most {MAX_PAGES} pages at a time.')
    if len(pages) == 1:
        return replace(pages[0], original_filename=sanitize_filename(uploads[0].name))
    # Include page order and an explicit contract version in the dedup key.
    digest = hashlib.sha256(('documents-v2:' + ':'.join(p.sha256 for p in pages)).encode()).hexdigest()
    return replace(pages[0], sha256=digest, pages=pages, original_bytes=total_bytes,
                   original_filename=sanitize_filename(uploads[0].name))


def _pdf_pages(data: bytes, remaining: int) -> list[PreparedImage]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise ImageRejected('PDF_UNAVAILABLE', 'PDF scanning is unavailable. Upload photos of the pages or enter prices manually.', 503)
    try:
        with _PDF_LOCK, pdfium.PdfDocument(data) as document:
            if not 1 <= len(document) <= remaining:
                raise ImageRejected('PAGE_LIMIT', f'Upload at most {MAX_PAGES} pages at a time.')
            result = []
            for index in range(len(document)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if not all(math.isfinite(v) and 0 < v <= 14400 for v in (width, height)):
                        raise ImageRejected('INVALID_PDF', 'This PDF has unsupported page dimensions.')
                    scale = min(2.0, settings.PRICE_LIST_GEMINI_LONG_EDGE / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                        output = io.BytesIO()
                        image.convert('RGB').save(output, format='JPEG', quality=90)
                        result.append(prepare_image(SimpleUploadedFile(f'page-{index + 1}.jpg', output.getvalue(), content_type='image/jpeg')))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            return result
    except ImageRejected:
        raise
    except Exception:
        raise ImageRejected('INVALID_PDF', 'This PDF cannot be read. Use an unlocked PDF or upload photos of its pages.')


def archive_pages(image: PreparedImage) -> tuple[str, bytes, int]:
    pages = image.pages or [image]
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for index, page in enumerate(pages, start=1):
            archive.writestr(f'{index}.jpg', page.provider_bytes)
    return f'{uuid.uuid4().hex}.zip', output.getvalue(), len(pages)


def read_source_page(job, page: int) -> bytes:
    if not job.source_document or not 1 <= page <= min(job.source_pages, MAX_PAGES):
        raise FileNotFoundError
    try:
        with job.source_document.open('rb') as stored, zipfile.ZipFile(stored) as archive:
            info = archive.getinfo(f'{page}.jpg')
            if info.file_size > settings.PRICE_LIST_UPLOAD_MAX_MB * 1024 * 1024:
                raise FileNotFoundError
            return archive.read(info)
    except zipfile.BadZipFile:
        raise FileNotFoundError from None
