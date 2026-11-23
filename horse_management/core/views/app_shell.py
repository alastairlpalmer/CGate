"""What the installed app needs that belongs to no page: the service
worker and the page for no signal (docs/MOBILE_APP_PLAN.md, item D4).

Both are public. Neither holds data: the worker is a list of addresses of
styles, scripts, fonts and icons, and the page is one message. The worker
stores no page of the app, so there is nothing of a horse, an owner or
money on the phone because of it.
"""

import json
import re
from functools import lru_cache

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET

from core.templatetags.cache_bust import app_version, static_v

WORKER_FILE = 'js/app_worker.js'

# What the "No signal" page needs to look like the app. If one of these
# cannot be stored, the worker does not install.
OFFLINE_STYLES = ('css/styles.css',)
OFFLINE_FILES = (
    'fonts/DMSans-Variable.woff2',
    'fonts/SourceSans3-Variable.woff2',
    'icons/icon-192.png',
    'icons/favicon-32.png',
)

# Addresses this worker leaves alone. C-vet has its own worker, and its
# own answer to no signal.
SKIP_PREFIXES = ('/c-vet/',)

_HEAD_FILE = re.compile(r"""{%\s*static_v\s+'((?:js|css)/[^']+)'\s*%}""")


@lru_cache(maxsize=1)
def head_files():
    """The styles and scripts every page loads, read from base.html.

    They are read from the template and not listed here: a script added
    to the page is then stored by the worker with no second list to keep.
    """
    head = (settings.BASE_DIR / 'templates' / 'base.html').read_text().split('</head>', 1)[0]
    return tuple(dict.fromkeys(_HEAD_FILE.findall(head)))


@lru_cache(maxsize=1)
def _worker_source():
    return (settings.BASE_DIR / 'static' / WORKER_FILE).read_text()


def shell_config():
    """What the worker is told: the version and the addresses to keep."""
    needed = [reverse('offline')]
    needed += [static_v(path) for path in OFFLINE_STYLES]
    needed += [static(path) for path in OFFLINE_FILES]
    wanted = [static_v(path) for path in head_files() if path not in OFFLINE_STYLES]
    return {
        'version': app_version(),
        'offline': reverse('offline'),
        'needed': needed,
        'wanted': wanted,
        'staticPrefix': settings.STATIC_URL,
        'skip': list(SKIP_PREFIXES),
    }


# What /sw.js is when the worker is switched off: a worker that deletes
# its store and removes itself. A phone that has the real worker asks for
# /sw.js again within a day, gets this one, and is clean after it.
REMOVAL_WORKER = """// The app's service worker is switched off (APP_WORKER_ENABLED).
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) {
    event.waitUntil(caches.keys().then(function (names) {
        return Promise.all(names.filter(function (name) {
            return name.indexOf('app-shell-') === 0;
        }).map(function (name) { return caches.delete(name); }));
    }).then(function () { return self.registration.unregister(); }));
});
"""


@require_GET
def service_worker(request):
    """The service worker script, at /sw.js.

    It is served from the root so that it can work for every page: a
    worker under /static/ could only work for addresses under /static/.
    No sign-in is needed, as for any script.
    """
    if not settings.APP_WORKER_ENABLED:
        response = HttpResponse(REMOVAL_WORKER, content_type='text/javascript; charset=utf-8')
        response.headers['Cache-Control'] = 'no-cache'
        return response
    if settings.DEBUG:
        _worker_source.cache_clear()
    # json.dumps writes the settings as a JavaScript value. "</" cannot
    # end a script here (this is a script file, not a page), and every
    # value is made by this module.
    body = f'self.APP_SHELL = {json.dumps(shell_config())};\n{_worker_source()}'
    response = HttpResponse(body, content_type='text/javascript; charset=utf-8')
    # The browser asks for this file to see if there is a new worker. It
    # must get the current one, not a copy it kept.
    response.headers['Cache-Control'] = 'no-cache'
    return response


@require_GET
def offline(request):
    """The page the worker shows when a page cannot load: no signal.

    The worker stores one copy of it and shows that copy to everyone who
    uses the phone, so it must say nothing about the person or the yard.
    """
    response = render(request, 'offline.html')
    response.headers['Cache-Control'] = 'no-cache'
    return response
