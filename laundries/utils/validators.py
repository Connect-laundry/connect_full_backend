# pyre-ignore[missing-module]
from django.core.exceptions import ValidationError
import os

def validate_file_upload(value):
    """
    Validator to protect against:
    - Large file uploads (>2MB)
    - Executable/malicious files (by extension and MIME type)
    """
    # 1. File Size Validation (Max 2MB)
    limit = 2 * 1024 * 1024
    if value.size > limit:
        raise ValidationError('File size too large. Size should not exceed 2 MB.')

    # 2. Extension Validation
    ext = os.path.splitext(value.name)[1]
    valid_extensions = ['.pdf', '.doc', '.docx', '.jpg', '.png', '.jpeg']
    if not ext.lower() in valid_extensions:
        raise ValidationError('Unsupported file extension.')

    # 3. Content Type (MIME) Validation
    # In production, use 'python-magic' if available for deeper inspection
    valid_mime_types = [
        'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'image/jpeg',
        'image/png',
    ]
    if hasattr(value, 'content_type') and value.content_type not in valid_mime_types:
        raise ValidationError('Invalid file type.')
