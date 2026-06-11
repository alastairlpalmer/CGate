"""Image upload normalisation helpers.

iPhones (and some Androids) upload photos as HEIC/HEIF by default. With
pillow-heif registered (see core.apps.CoreConfig.ready) Django can validate
them, but most browsers still can't *display* HEIC, so uploads are converted
to JPEG before they're saved. Other formats pass through untouched.
"""

import io
from pathlib import Path

from django.core.files.uploadedfile import InMemoryUploadedFile

_HEIF_FORMATS = {'HEIF', 'HEIC', 'AVIF'}


def heic_to_jpeg(upload):
    """Return ``upload`` converted to JPEG if it is HEIC/HEIF, else unchanged.

    Safe to call from a form's ``clean_<field>`` with whatever the ImageField
    produced: ``None`` (no file), ``False`` (clear checkbox) and existing
    ``FieldFile`` values are returned as-is.
    """
    from PIL import Image, ImageOps

    if not upload or not hasattr(upload, 'read'):
        return upload

    try:
        upload.seek(0)
        image = Image.open(upload)
        detected = (image.format or '').upper()
    except Exception:
        # Not something Pillow can open — leave it for field validation
        # to reject with a proper error message.
        upload.seek(0)
        return upload

    if detected not in _HEIF_FORMATS:
        upload.seek(0)
        return upload

    # Apply the EXIF rotation before it is lost in conversion, then flatten
    # to RGB (JPEG has no alpha channel).
    image = ImageOps.exif_transpose(image)
    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')

    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=90)
    buffer.seek(0)

    return InMemoryUploadedFile(
        file=buffer,
        field_name=getattr(upload, 'field_name', None),
        name=Path(upload.name).stem + '.jpg',
        content_type='image/jpeg',
        size=buffer.getbuffer().nbytes,
        charset=None,
    )
