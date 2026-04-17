"""Cache-busting template tag for static files.

Appends `?v=<commit-sha>` to static URLs so browsers fetch fresh assets
after each deploy. Reads Vercel's VERCEL_GIT_COMMIT_SHA env var at
module load; falls back to "dev" when running outside Vercel.

Uses Django's {% static %} helper internally so the underlying URL
resolution is identical — only the query string differs.
"""

import os

from django import template
from django.templatetags.static import static as static_url

register = template.Library()


# Short SHA prefix so the cache key changes on every deploy but stays
# compact. Computed once at module import: Vercel's Lambda lifecycle
# means this gets re-evaluated on each cold start, which matches deploy
# boundaries.
_VERSION = (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "dev")[:8]


@register.simple_tag
def static_v(path):
    """Like {% static %} but with a deploy-unique cache-busting query."""
    url = static_url(path)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}v={_VERSION}"
