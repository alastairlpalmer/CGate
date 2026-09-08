"""Cache-busting template tag for static files.

Appends `?v=<version>` to static URLs so browsers fetch fresh assets after
each deploy. The version is the host's git commit (Vercel:
VERCEL_GIT_COMMIT_SHA, Railway: RAILWAY_GIT_COMMIT_SHA). On a host that
sets neither it is a digest of the static files themselves, so any change
to a stylesheet or a script still changes the URL; "dev" only when there
are no static files to hash.

The fallback matters: with WHITENOISE_MAX_AGE set to a year, a version
string that never changes (as happened on Railway before the Railway var
was added here) pins clients to the first stylesheet they ever saw.

Uses Django's {% static %} helper internally so the underlying URL
resolution is identical — only the query string differs.
"""

import hashlib
import os
from pathlib import Path

from django import template
from django.conf import settings
from django.templatetags.static import static as static_url

register = template.Library()


def static_digest(roots=None):
    """A SHA-1 of every file under the static source directories (or
    STATIC_ROOT when there are none): path and content, in path order.
    Empty when nothing is found. About a millisecond per megabyte, once."""
    if roots is None:
        roots = list(getattr(settings, 'STATICFILES_DIRS', None) or [])
        if not roots and getattr(settings, 'STATIC_ROOT', None):
            roots = [settings.STATIC_ROOT]
    # usedforsecurity=False: this digest is a cache-busting version
    # string, never a security control. The flag tells static
    # analysis (and a FIPS build of OpenSSL) that SHA-1 is fine here.
    digest = hashlib.sha1(usedforsecurity=False)
    found = False
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            found = True
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest() if found else ''


def version():
    """Short so the cache key stays compact; unique per deploy."""
    return (
        os.environ.get("VERCEL_GIT_COMMIT_SHA")
        or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or static_digest()
        or "dev"
    )[:8]


# Computed once at module import, which matches deploy boundaries on both
# Vercel (per cold start) and Railway (per container).
_VERSION = version()


@register.simple_tag
def static_v(path):
    """Like {% static %} but with a deploy-unique cache-busting query."""
    url = static_url(path)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}v={_VERSION}"
