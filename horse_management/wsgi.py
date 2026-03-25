"""
WSGI config for horse_management project.
"""

import os
import sys
import json
from pathlib import Path

# Ensure the horse_management/ directory is at the FRONT of sys.path so
# bare imports (core, billing, health, etc.) resolve to the correct inner
# packages rather than any stale repo-root duplicates.
_this_dir = Path(__file__).resolve().parent
_project_dir = str(_this_dir)

# Insert at position 0 to take priority over /var/task
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'horse_management.settings')

# Try to boot Django; capture the error if it fails
_django_app = None
_boot_error = None
try:
    from django.core.wsgi import get_wsgi_application
    _django_app = get_wsgi_application()
    # Run pending migrations on serverless cold-start (Vercel)
    from django.core.management import call_command
    call_command('migrate', '--noinput')
except Exception:
    import traceback
    _boot_error = traceback.format_exc()


def application(environ, start_response):
    """WSGI entrypoint."""
    # If Django failed to boot, return a generic error (no internals leaked)
    if _django_app is None:
        body = json.dumps({
            'error': 'Application failed to start.',
            'detail': _boot_error or 'Unknown error',
        }).encode()
        start_response('500 Internal Server Error', [
            ('Content-Type', 'application/json'),
            ('Content-Length', str(len(body))),
        ])
        return [body]

    return _django_app(environ, start_response)


app = application
