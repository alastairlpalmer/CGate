"""Checks on uploaded files, and how stored files are served.

docs/LAUNCH_READINESS_PLAN.md, P1 work package 8.

The model fields already limit the extension and the size. An extension is
only a name, so ``validate_file_content`` also reads the first bytes of
each new upload and checks that they are what the extension says: a
``.pdf`` must start like a PDF. An HTML page or a script renamed to
``passport.pdf`` is refused.

``content_disposition`` says whether a stored file may open in the browser
(PDFs and the images a browser can show) or must download. The media
storage (core/storage.py) and the local media view use it.
"""

import zipfile

from django.core.exceptions import ValidationError

# The first bytes of each allowed file type.
_JPEG = (b'\xff\xd8\xff',)
_PNG = (b'\x89PNG\r\n\x1a\n',)
_PDF = (b'%PDF-',)
_OLE = (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',)  # Old Word .doc
_ZIP = (b'PK\x03\x04',)                           # .docx is a zip

# ISO media "brands" used by HEIC and HEIF photos (iPhone camera).
_HEIF_BRANDS = {b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'mif1', b'msf1'}

# Opened in the browser. Everything else is sent as a download.
INLINE_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'webp'}

_HEAD_BYTES = 32


def _extension(name):
    return name.rsplit('.', 1)[-1].lower() if '.' in (name or '') else ''


def _matches(extension, head, file):
    if extension in ('jpg', 'jpeg'):
        return head.startswith(_JPEG)
    if extension == 'png':
        return head.startswith(_PNG)
    if extension == 'pdf':
        # Some PDF writers put a few bytes before the header; readers
        # accept it anywhere in the first kilobyte, and so does this.
        return head.startswith(_PDF) or b'%PDF-' in _read_head(file, 1024)
    if extension == 'webp':
        return head[:4] == b'RIFF' and head[8:12] == b'WEBP'
    if extension in ('heic', 'heif'):
        return head[4:8] == b'ftyp' and head[8:12] in _HEIF_BRANDS
    if extension == 'doc':
        return head.startswith(_OLE)
    if extension == 'docx':
        return head.startswith(_ZIP) and _is_word_zip(file)
    return False


def _read_head(file, size):
    file.seek(0)
    head = file.read(size)
    file.seek(0)
    return head


def _is_word_zip(file):
    try:
        file.seek(0)
        with zipfile.ZipFile(file) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    finally:
        file.seek(0)
    return '[Content_Types].xml' in names and 'word/document.xml' in names


def validate_file_content(value):
    """Refuse a new upload whose content does not match its extension.

    Only new uploads are read. A file already in storage was checked when
    it came in, and reading it again would fetch it from the bucket on
    every save of the record.
    """
    if getattr(value, '_committed', False):
        return
    extension = _extension(value.name)
    head = _read_head(value, _HEAD_BYTES)
    if not _matches(extension, head, value):
        raise ValidationError(
            f'This file is not a real .{extension} file. Save it again as '
            f'.{extension} (or choose the right type), then upload it.'
        )
    if extension in IMAGE_EXTENSIONS:
        validate_image_pixels(value)


IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'}

# Above this, an image is refused before it is decoded. Bytes are not the
# limit that matters: a 17 KB PNG of one colour can be 20,000 x 20,000
# pixels, and decoding it takes about 1.6 GB. 50 million pixels is more
# than any phone camera makes. core.apps also sets Pillow's own limit.
MAX_IMAGE_PIXELS = 50_000_000


def validate_image_pixels(value):
    """Refuse an image with more than MAX_IMAGE_PIXELS, reading only its header."""
    from PIL import Image

    try:
        value.seek(0)
        with Image.open(value) as image:
            width, height = image.size
    except Image.DecompressionBombError:
        width = height = MAX_IMAGE_PIXELS
    except Exception:
        # Not readable as an image: the ImageField or the content check says so.
        return
    finally:
        value.seek(0)
    if width * height > MAX_IMAGE_PIXELS:
        raise ValidationError(
            'This image is too large. Upload a photo of at most 50 million pixels '
            '(for example 8000 x 6000).'
        )


# The Content-Type each stored file is served with, from its extension.
# Never from the upload: the browser that sent the file chose that, and a
# "passport.pdf" sent as text/html would open as a page from the bucket.
_CONTENT_TYPES = {
    'pdf': 'application/pdf',
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'png': 'image/png',
    'webp': 'image/webp',
    'heic': 'image/heic', 'heif': 'image/heif',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'csv': 'text/csv',
}


def content_type(name):
    """The type a stored file is served with; unknown types download."""
    return _CONTENT_TYPES.get(_extension(name), 'application/octet-stream')


def content_disposition(name):
    """'inline' for files the browser may open itself, else 'attachment'."""
    return 'inline' if _extension(name) in INLINE_EXTENSIONS else 'attachment'
