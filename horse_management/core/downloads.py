"""The Content-Disposition header for a file the app sends.

An owner's name goes into some file names ("Statement - Siobhán O'Brien").
Put straight into the header, a quote ends the name early, and a name with
accents is not valid in the plain ``filename=`` part at all. Browsers then
save "statement-siobh" or a random name.

``attachment_header`` sends two names, as RFC 6266 says: a plain ASCII one
for old clients, and the real name in UTF-8 (``filename*``), which every
current browser uses.
"""

import re
import unicodedata
from urllib.parse import quote

# Characters that are never safe in a saved file name on some system.
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|;]+')
_NOT_ASCII_SAFE = re.compile(r'[^A-Za-z0-9._\- ]+')


def safe_filename(name, default='download'):
    """``name`` with path separators, quotes and control characters removed."""
    cleaned = _UNSAFE.sub('-', str(name or '')).strip(' .-')
    return re.sub(r'-{2,}', '-', cleaned)[:150] or default


def _ascii(name):
    plain = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    plain = _NOT_ASCII_SAFE.sub('-', plain).strip(' .-')
    return re.sub(r'-{2,}', '-', plain) or 'download'


def attachment_header(filename, inline=False):
    """The full header value: ``attachment; filename="..."; filename*=...``."""
    name = safe_filename(filename)
    stem, dot, extension = name.rpartition('.')
    fallback = f'{_ascii(stem)}.{_ascii(extension)}' if dot and stem else _ascii(name)
    kind = 'inline' if inline else 'attachment'
    if fallback == name:
        return f'{kind}; filename="{name}"'
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
