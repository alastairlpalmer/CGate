"""Which scripts a page loads: directly, or inside js/app.bundle.js.

Every page's scripts are joined into one file in the order of
static/js/bundle.txt (tools/build_js.mjs). Tests that ask "does the page
load X" use this, so they keep checking the same thing.
"""

from pathlib import Path

from django.conf import settings


def bundle_list():
    text = (Path(settings.BASE_DIR) / 'static' / 'js' / 'bundle.txt').read_text()
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]


def page_loads(html, name):
    """True if ``html`` loads ``js/<name>``, by itself or in the bundle."""
    path = f'js/{name}'
    if f'/static/{path}?v=' in html:
        return True
    return '/static/js/app.bundle.js?v=' in html and path in bundle_list()
