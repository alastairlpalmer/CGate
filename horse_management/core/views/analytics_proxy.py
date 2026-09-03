"""Same-origin reverse proxy for PostHog.

Ad blockers and privacy shields block requests to ``*.posthog.com`` by
default, so anyone running one is invisible to analytics. Routing the traffic
through this app's own domain removes that: the browser only ever talks to
``/ingest/...`` here, and this view forwards to PostHog.

Two upstreams, chosen by path:

* ``/ingest/static/...`` → the asset host (``array.js`` and its lazy chunks)
* everything else       → the event host (``/i/v0/e/``, ``/s/``, ``/flags/``)

Security notes:

* The browser's cookies are never forwarded. The Django session cookie must
  not leave this origin.
* Only GET, HEAD, POST and OPTIONS are accepted. Paths containing ``..`` are
  refused. The upstream host is fixed by settings, so this cannot be turned
  into an open proxy.
* CSRF is exempt because the request comes from posthog-js, which cannot
  carry a Django token. That is safe: the view has no side effects here and
  writes only to PostHog, which authenticates by the public project key.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

from core import analytics

logger = logging.getLogger('core')

ALLOWED_METHODS = ('GET', 'HEAD', 'POST', 'OPTIONS')

# Request headers worth passing upstream. Everything else, cookies included,
# stays here.
FORWARD_REQUEST_HEADERS = (
    'Content-Type',
    'Accept',
    'User-Agent',
    'Referer',
)

# Response headers passed back. Content-Length is set by Django and
# Content-Encoding is dropped because ``requests`` has already decoded the
# body. Set-Cookie is dropped on principle.
FORWARD_RESPONSE_HEADERS = (
    'Content-Type',
    'Cache-Control',
    'ETag',
    'Last-Modified',
    'Expires',
    'Vary',
)

UPSTREAM_TIMEOUT = 10  # seconds


def _client_ip(request) -> str:
    """Original client IP. Railway and Vercel put it in X-Forwarded-For."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _upstream_for(path: str) -> str:
    if path.startswith('static/'):
        return settings.POSTHOG_ASSET_HOST
    return settings.POSTHOG_HOST


@csrf_exempt
def posthog_proxy(request, upstream_path: str = ''):
    if not analytics.is_enabled() or not settings.POSTHOG_PROXY:
        raise Http404
    if request.method not in ALLOWED_METHODS:
        return HttpResponseNotAllowed(ALLOWED_METHODS)
    if '..' in upstream_path or upstream_path.startswith('/'):
        raise Http404

    url = f'{_upstream_for(upstream_path).rstrip("/")}/{upstream_path}'

    headers = {}
    for name in FORWARD_REQUEST_HEADERS:
        value = request.headers.get(name)
        if value:
            headers[name] = value
    headers['X-Forwarded-For'] = _client_ip(request)

    try:
        upstream = requests.request(
            request.method,
            url,
            params=request.GET.urlencode() or None,
            data=request.body if request.method == 'POST' else None,
            headers=headers,
            timeout=UPSTREAM_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        # Analytics must never surface as an app error. Log and tell the
        # browser to stop retrying this one.
        logger.warning('PostHog proxy: upstream %s failed: %s', url, exc)
        return HttpResponse(status=502)

    response = HttpResponse(upstream.content, status=upstream.status_code)
    for name in FORWARD_RESPONSE_HEADERS:
        value = upstream.headers.get(name)
        if value:
            response[name] = value
    return response
