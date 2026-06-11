"""Image upload normalisation helpers.

iPhones (and some Androids) upload photos as HEIC/HEIF by default. With
pillow-heif registered (see core.apps.CoreConfig.ready) Django can validate
them, but most browsers still can't *display* HEIC, so uploads are converted
to JPEG before they're saved. Other formats pass through untouched.
"""

import io
import logging
from pathlib import Path

from django.core.files.uploadedfile import InMemoryUploadedFile, UploadedFile

logger = logging.getLogger(__name__)

_HEIF_FORMATS = {'HEIF', 'HEIC', 'AVIF'}

# Converted photos are capped at this size (longest edge, px) so the JPEG
# stays comfortably under the app's 5MB upload validator — a full-resolution
# phone photo re-encoded as JPEG can exceed the original HEIC's size.
_MAX_DIMENSION = 2560


def heic_to_jpeg(upload):
    """Return ``upload`` converted to JPEG if it is HEIC/HEIF, else unchanged.

    Only acts on freshly uploaded files. Anything else a form's
    ``clean_<field>`` may hand us — ``None`` (no file), ``False`` (the
    "clear" checkbox) or the model's existing ``FieldFile`` when the photo
    wasn't changed — passes straight through without touching storage, so a
    stale database path whose file no longer exists can't break the save.

    Conversion failures fall back to the original upload rather than
    raising: it has already passed field validation at that point.
    """
    from PIL import Image, ImageOps

    if not isinstance(upload, UploadedFile):
        return upload

    try:
        upload.seek(0)
        image = Image.open(upload)
        if (image.format or '').upper() not in _HEIF_FORMATS:
            upload.seek(0)
            return upload

        # Apply the EXIF rotation before it is lost in conversion, then
        # flatten to RGB (JPEG has no alpha channel).
        image = ImageOps.exif_transpose(image)
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        image.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION))

        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
    except Exception:
        logger.exception("HEIC conversion failed for upload %r", upload.name)
        upload.seek(0)
        return upload

    buffer.seek(0)
    return InMemoryUploadedFile(
        file=buffer,
        field_name=getattr(upload, 'field_name', None),
        name=Path(upload.name).stem + '.jpg',
        content_type='image/jpeg',
        size=buffer.getbuffer().nbytes,
        charset=None,
    )
