"""Cache-busting template tag for static files.

Appends `?v=<commit-sha>` to static URLs so browsers fetch fresh assets
after each deploy. Reads the host's git-commit env var at module load
(Vercel: VERCEL_GIT_COMMIT_SHA, Railway: RAILWAY_GIT_COMMIT_SHA); falls
back to "dev" when running locally.

The fallback matters: with WHITENOISE_MAX_AGE set to a year, a version
string that never changes (as happened on Railway before the Railway var
was added here) pins clients to the first stylesheet they ever saw.

Uses Django's {% static %} helper internally so the underlying URL
resolution is identical — only the query string differs.
"""

import os

from django import template
from django.templatetags.static import static as static_url

register = template.Library()


# Short SHA prefix so the cache key changes on every deploy but stays
# compact. Computed once at module import, which matches deploy
# boundaries on both Vercel (per cold start) and Railway (per container).
_VERSION = (
    os.environ.get("VERCEL_GIT_COMMIT_SHA")
    or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    or "dev"
)[:8]


@register.simple_tag
def static_v(path):
    """Like {% static %} but with a deploy-unique cache-busting query."""
    url = static_url(path)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}v={_VERSION}"
