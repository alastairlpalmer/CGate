"""
Custom CSRF failure view.

Shows Django's CSRF rejection reason plus request diagnostics on the 403
response so we can troubleshoot without enabling DEBUG in production.

Wrapped in try/except so any unexpected attribute access (e.g. raw POST
body already consumed) cannot escalate a 403 into a 500.
"""

import logging
import traceback

from django.http import HttpResponseForbidden
from django.utils.html import escape

logger = logging.getLogger(__name__)


def _safe_get(fn, default="(error)"):
    try:
        return fn()
    except Exception:
        return default


def csrf_failure(request, reason=""):
    try:
        origin = request.META.get("HTTP_ORIGIN", "(none)")
        referer = request.META.get("HTTP_REFERER", "(none)")
        host = _safe_get(request.get_host)
        has_cookie = _safe_get(lambda: "csrftoken" in request.COOKIES, False)
        has_form_token = _safe_get(
            lambda: "csrfmiddlewaretoken" in (request.POST or {}), False
        )
        is_secure = _safe_get(request.is_secure, False)
        xfp = request.META.get("HTTP_X_FORWARDED_PROTO", "(none)")
        xfh = request.META.get("HTTP_X_FORWARDED_HOST", "(none)")
        method = request.method
        path = request.path

        body = f"""<!doctype html>
<html><head><title>CSRF 403</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:-apple-system,system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem;line-height:1.5;">
<h1>CSRF verification failed</h1>
<p><b>Reason:</b> <code>{escape(reason)}</code></p>
<h3>Request diagnostics</h3>
<ul>
  <li><b>method:</b> <code>{escape(method)}</code></li>
  <li><b>path:</b> <code>{escape(path)}</code></li>
  <li><b>host (get_host):</b> <code>{escape(str(host))}</code></li>
  <li><b>origin:</b> <code>{escape(origin)}</code></li>
  <li><b>referer:</b> <code>{escape(referer)}</code></li>
  <li><b>is_secure():</b> <code>{is_secure}</code></li>
  <li><b>X-Forwarded-Proto:</b> <code>{escape(xfp)}</code></li>
  <li><b>X-Forwarded-Host:</b> <code>{escape(xfh)}</code></li>
  <li><b>csrftoken cookie present:</b> <code>{has_cookie}</code></li>
  <li><b>csrfmiddlewaretoken form field present:</b> <code>{has_form_token}</code></li>
</ul>
<p style="color:#888;font-size:0.85em;">Diagnostic view — remove once CSRF is stable.</p>
</body></html>"""
        return HttpResponseForbidden(body)
    except Exception:
        # Never let the failure view itself 500 — log and return a minimal 403.
        logger.exception("csrf_failure view raised")
        return HttpResponseForbidden(
            "CSRF verification failed. (diagnostic view error; check server logs)"
        )
