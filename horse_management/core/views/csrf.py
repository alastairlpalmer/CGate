"""
Custom CSRF failure view.

Shows the exact Django reason string on a 403 so we can diagnose
verification failures in production without enabling DEBUG.

The reason strings come from django.middleware.csrf.REASON_* constants
and are not sensitive (they describe the validation outcome, not any
user secret).
"""

from django.http import HttpResponseForbidden
from django.utils.html import escape


def csrf_failure(request, reason=""):
    origin = request.META.get("HTTP_ORIGIN", "(none)")
    referer = request.META.get("HTTP_REFERER", "(none)")
    host = request.get_host()
    has_cookie = "csrftoken" in request.COOKIES
    has_form_token = "csrfmiddlewaretoken" in (request.POST or {})
    is_secure = request.is_secure()
    xfp = request.META.get("HTTP_X_FORWARDED_PROTO", "(none)")
    xfh = request.META.get("HTTP_X_FORWARDED_HOST", "(none)")

    body = f"""<!doctype html>
<html><head><title>CSRF 403</title></head>
<body style="font-family: -apple-system, system-ui, sans-serif; max-width: 680px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5;">
<h1>CSRF verification failed</h1>
<p><b>Reason:</b> <code>{escape(reason)}</code></p>
<h3>Request diagnostics</h3>
<ul>
  <li><b>host (get_host):</b> <code>{escape(host)}</code></li>
  <li><b>origin:</b> <code>{escape(origin)}</code></li>
  <li><b>referer:</b> <code>{escape(referer)}</code></li>
  <li><b>is_secure():</b> <code>{is_secure}</code></li>
  <li><b>X-Forwarded-Proto:</b> <code>{escape(xfp)}</code></li>
  <li><b>X-Forwarded-Host:</b> <code>{escape(xfh)}</code></li>
  <li><b>csrftoken cookie present:</b> <code>{has_cookie}</code></li>
  <li><b>csrfmiddlewaretoken form field present:</b> <code>{has_form_token}</code></li>
</ul>
<p style="color:#888;font-size:0.85em;">Diagnostic view — remove after troubleshooting.</p>
</body></html>"""
    return HttpResponseForbidden(body)
